"""
BMW Dashboard — FastAPI Backend v3.0.0
Verbesserungen:
- Smarter Fetch: max 1x pro Stunde automatisch, nie beim Startup
- Rate Limit Tracking: zählt verbrauchte Requests
- Cache: zeigt letzte Daten auch bei Rate Limit
- Auto-Login: Cookie hält 30 Tage
"""
import os, time, json, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

import database as db
import bmw_api as api

VERSION        = "3.0.0"
CLIENT_ID      = os.getenv("BMW_CLIENT_ID", "5f4b2906-4dc0-4874-88c6-c84accdcf284")
VIN            = os.getenv("BMW_VIN",       "WBY21HD080FU24651")
PASSWORD       = os.getenv("DASHBOARD_PASSWORD", "bmw-i4-2024")
CONTAINER_NAME = "bmw_i4_dashboard"

ENV_REFRESH_TOKEN = os.getenv("BMW_REFRESH_TOKEN", "")
ENV_ACCESS_TOKEN  = os.getenv("BMW_ACCESS_TOKEN", "")
ENV_GCID          = os.getenv("BMW_GCID", "")

# Rate Limit Tracking (50/Tag)
_rate_limit_count = 0
_rate_limit_reset = 0  # Unix timestamp wann Reset

app = FastAPI(title="BMW i4 Dashboard", docs_url=None, redoc_url=None)
db.init_db()

_last_fetch = 0
_fetch_lock = threading.Lock()


# ── Rate Limit Tracking ──────────────────────────────────────

def track_request(count=1):
    """Zählt verbrauchte API Requests."""
    global _rate_limit_count, _rate_limit_reset
    now = time.time()
    # Reset um Mitternacht UTC
    today_midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    if now < today_midnight or _rate_limit_reset < today_midnight:
        _rate_limit_count = 0
        _rate_limit_reset = today_midnight + 86400
    _rate_limit_count += count

def get_rate_limit_info():
    return {
        "used": _rate_limit_count,
        "remaining": max(0, 50 - _rate_limit_count),
        "limit": 50
    }


# ── DCF State ────────────────────────────────────────────────

def save_dcf_state(state: dict):
    conn = db.get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS dcf_state (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("DELETE FROM dcf_state")
    conn.execute("INSERT INTO dcf_state (id, data) VALUES (1, ?)", (json.dumps(state),))
    conn.commit(); conn.close()

def load_dcf_state() -> dict:
    try:
        conn = db.get_db()
        conn.execute("CREATE TABLE IF NOT EXISTS dcf_state (id INTEGER PRIMARY KEY, data TEXT)")
        row = conn.execute("SELECT data FROM dcf_state WHERE id=1").fetchone()
        conn.close()
        return json.loads(row[0]) if row else {}
    except:
        return {}

def clear_dcf_state():
    try:
        conn = db.get_db()
        conn.execute("DELETE FROM dcf_state")
        conn.commit(); conn.close()
    except:
        pass


# ── Token Management ─────────────────────────────────────────

def get_token() -> str | None:
    """Holt Token: DB > ENV_ACCESS_TOKEN > Refresh"""
    tokens = db.load_tokens_db()
    if tokens:
        age = time.time() - tokens.get("saved_at", 0)
        if age < tokens.get("expires_in", 3600) - 60:
            return tokens["access_token"]
    if ENV_ACCESS_TOKEN:
        return ENV_ACCESS_TOKEN
    refresh = (tokens or {}).get("refresh_token") or ENV_REFRESH_TOKEN
    if refresh:
        new_t = api.refresh_token(CLIENT_ID, refresh)
        if new_t:
            new_t["gcid"] = ENV_GCID
            db.save_tokens_db(new_t)
            return new_t["access_token"]
    return None


# ── Auth ─────────────────────────────────────────────────────

def check_password(request: Request) -> bool:
    return request.cookies.get("auth_token") == PASSWORD

def require_auth(request: Request):
    if not check_password(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Smart Fetch ──────────────────────────────────────────────

def fetch_all_data(force: bool = False):
    """
    Ruft BMW API Daten ab.
    force=False: nur wenn letzte Abfrage > 1 Stunde her
    force=True:  immer (manueller Abruf)
    """
    global _last_fetch

    # Nicht automatisch beim Start — nur wenn explizit aufgerufen
    if not force:
        age = time.time() - _last_fetch
        if age < 3600:
            snap = db.get_latest_snapshot()
            return snap["data"] if snap else {"error": "no_data"}

    token = get_token()
    if not token:
        return {"error": "no_token", "message": "Bitte BMW verbinden"}

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vin": VIN,
        "errors": []
    }

    # Basic Data (1 Request)
    try:
        basic = api.get_basic_data(token, VIN)
        track_request(1)
        if basic:
            result["basicData"] = basic
            print(f"  ✓ basicData: {basic.get('modelName','?')}")
        else:
            print("  ✗ basicData: leer")
            result["errors"].append("basicData leer")
    except Exception as e:
        print(f"  ✗ basicData: {e}")
        result["errors"].append(f"basicData: {e}")

    # Container + Telemetrie (2 Requests: get + create/get)
    try:
        container_id = api.get_or_create_container(token, CONTAINER_NAME, api.CONTAINER_KEYS)
        track_request(2)
        if container_id:
            tel = api.get_telematic_data(token, VIN, container_id)
            track_request(1)
            result["telemetry"] = tel
            print(f"  ✓ Telemetrie: {len(tel)} Keys")
            for key, entry in tel.items():
                if isinstance(entry, dict):
                    db.save_telemetry(key, entry.get("value",""),
                                      entry.get("unit",""), entry.get("timestamp",""))
        else:
            result["errors"].append("Container None")
    except Exception as e:
        print(f"  ✗ Container: {e}")
        result["errors"].append(f"Container: {e}")

    # Ladehistorie (1 Request)
    try:
        sessions = api.get_charging_history(token, VIN, days=90)
        track_request(1)
        if sessions:
            db.save_charging_sessions(sessions)
            result["new_sessions"] = len(sessions)
            print(f"  ✓ {len(sessions)} Sessions")
    except Exception as e:
        print(f"  ✗ Ladehistorie: {e}")

    # Reifen (1 Request)
    try:
        tyres = api.get_tyre_diagnosis(token, VIN)
        track_request(1)
        if tyres:
            result["tyres"] = tyres
            print("  ✓ Reifen OK")
    except Exception as e:
        print(f"  ✗ Reifen: {e}")

    # Ladeorte (1 Request)
    try:
        lbcs = api.get_lbcs(token, VIN)
        track_request(1)
        result["chargingLocations"] = lbcs
    except Exception as e:
        print(f"  ✗ Ladeorte: {e}")

    result["rate_limit"] = get_rate_limit_info()
    print(f"  → Requests heute: {_rate_limit_count}/50")

    if not result["errors"]:
        del result["errors"]

    db.save_snapshot(result)
    _last_fetch = time.time()
    return result


# ── API Routes ───────────────────────────────────────────────

@app.get("/api/version")
def version():
    return {"version": VERSION, "vin": VIN,
            "env_token": "ok" if ENV_ACCESS_TOKEN else "missing"}

@app.get("/api/status")
def status(request: Request):
    token = get_token()
    snap = db.get_latest_snapshot()
    last_ts = snap["data"].get("timestamp") if snap else None
    return {
        "version": VERSION,
        "authenticated": check_password(request),
        "bmw_connected": bool(token),
        "last_fetch": _last_fetch,
        "last_fetch_ts": last_ts,
        "rate_limit": get_rate_limit_info(),
        "vin": VIN
    }

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if body.get("password") == PASSWORD:
        response = JSONResponse({"ok": True, "version": VERSION})
        response.set_cookie("auth_token", PASSWORD, httponly=True,
                            samesite="lax", max_age=60*60*24*30)
        return response
    raise HTTPException(401, "Falsches Passwort")

@app.get("/api/snapshot")
def get_snapshot(request: Request):
    require_auth(request)
    snap = db.get_latest_snapshot()
    if not snap:
        return {"error": "no_data"}
    data = snap["data"]
    data["rate_limit"] = get_rate_limit_info()
    data["cached"] = True
    return data

@app.post("/api/fetch")
def trigger_fetch(request: Request):
    """Manueller Abruf — immer frische Daten."""
    require_auth(request)
    remaining = get_rate_limit_info()["remaining"]
    if remaining < 5:
        # Rate Limit fast aufgebraucht — gib Cache zurück
        snap = db.get_latest_snapshot()
        if snap:
            data = snap["data"]
            data["rate_limit"] = get_rate_limit_info()
            data["warning"] = f"Rate Limit fast erreicht ({remaining} verbleibend) — zeige gespeicherte Daten"
            return data
    with _fetch_lock:
        result = fetch_all_data(force=True)
    return result

@app.get("/api/charging")
def charging_history(request: Request, limit: int = 100, offset: int = 0):
    require_auth(request)
    sessions = db.get_charging_sessions(limit=limit, offset=offset)
    stats = db.get_charging_stats()
    return {"sessions": sessions, "stats": stats}

@app.get("/api/telemetry/{key:path}")
def telemetry_history(key: str, request: Request, limit: int = 100):
    require_auth(request)
    return {"key": key, "data": db.get_telemetry_history(key, limit=limit)}

@app.get("/api/test")
def test_api(request: Request):
    require_auth(request)
    token = get_token()
    if not token:
        return {"error": "no_token"}
    import requests as req
    results = {"version": VERSION, "rate_limit": get_rate_limit_info()}
    for name, url in [
        ("mappings",  f"{api.BASE_API}/customers/vehicles/mappings"),
        ("basicData", f"{api.BASE_API}/customers/vehicles/{VIN}/basicData"),
        ("tyres",     f"{api.BASE_API}/customers/vehicles/{VIN}/smartMaintenanceTyreDiagnosis"),
    ]:
        r = req.get(url, headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/json", "x-version": "v1"}, timeout=10)
        track_request(1)
        try: body = r.json()
        except: body = r.text[:200]
        results[name] = {"status": r.status_code, "body": body}
    return results

@app.post("/api/bmw/auth/start")
def bmw_auth_start(request: Request):
    require_auth(request)
    dcf = api.get_device_code(CLIENT_ID)
    if not dcf:
        raise HTTPException(500, "Device Code Flow fehlgeschlagen")
    save_dcf_state(dcf)
    return {
        "user_code":        dcf["user_code"],
        "verification_uri": dcf["verification_uri"],
        "login_url":        f"{dcf['verification_uri']}?user_code={dcf['user_code']}",
        "expires_in":       dcf.get("expires_in", 300)
    }

@app.post("/api/bmw/auth/poll")
def bmw_auth_poll(request: Request):
    require_auth(request)
    state = load_dcf_state()
    if not state.get("device_code"):
        raise HTTPException(400, "Kein aktiver Auth-Flow")
    tokens = api.poll_token(CLIENT_ID, state["device_code"], state["code_verifier"])
    if tokens:
        db.save_tokens_db(tokens)
        clear_dcf_state()
        return {"ok": True, "gcid": tokens.get("gcid")}
    return {"ok": False, "pending": True}


# ── Static + SPA ─────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/favicon.ico")
async def favicon():
    raise HTTPException(404)

@app.get("/api/{path:path}")
async def api_404(path: str):
    raise HTTPException(404, f"/api/{path} not found")

@app.get("/{full_path:path}", response_class=HTMLResponse)
async def serve_spa(full_path: str, request: Request):
    index = static_dir / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse(f"<h1>BMW Dashboard v{VERSION}</h1>")

@app.on_event("startup")
async def startup():
    print(f"╔══════════════════════════════════╗")
    print(f"║  BMW Dashboard v{VERSION}            ║")
    print(f"╚══════════════════════════════════╝")
    print(f"  VIN:   {VIN}")
    print(f"  Token: {'✓' if ENV_ACCESS_TOKEN else '✗'}")
    # KEIN automatischer Fetch beim Start — spart Rate Limit!

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
