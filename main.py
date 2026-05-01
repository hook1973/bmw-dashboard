"""
BMW Dashboard — FastAPI Backend
"""
import os, time, json, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Response, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn

import database as db
import bmw_api as api

# ── Config ───────────────────────────────────────────────────
CLIENT_ID = os.getenv("BMW_CLIENT_ID", "5f4b2906-4dc0-4874-88c6-c84accdcf284")
VIN       = os.getenv("BMW_VIN",       "WBY21HD080FU24651")
PASSWORD  = os.getenv("DASHBOARD_PASSWORD", "bmw-i4-2024")
CONTAINER_NAME = "bmw_i4_dashboard"

app = FastAPI(title="BMW i4 Dashboard", docs_url=None, redoc_url=None)

# ── Init ─────────────────────────────────────────────────────
db.init_db()

# Device Code Flow State (im Speicher)
_dcf_state = {}
_last_fetch = 0
_fetch_lock = threading.Lock()


# ── Auth ─────────────────────────────────────────────────────

def check_password(request: Request) -> bool:
    token = request.cookies.get("auth_token")
    return token == PASSWORD

def require_auth(request: Request):
    if not check_password(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Background Fetch ─────────────────────────────────────────

def fetch_all_data():
    """Holt alle Fahrzeugdaten und speichert sie in DB."""
    global _last_fetch
    tokens = db.load_tokens_db()
    token = api.get_valid_token(CLIENT_ID, tokens)
    if not token:
        return {"error": "no_token"}

    # Tokens ggf. erneuern und speichern
    if api._token_cache and api._token_cache != tokens:
        db.save_tokens_db(api._token_cache)

    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "vin": VIN}

    # Basic Data
    basic = api.get_basic_data(token, VIN)
    if basic:
        result["basicData"] = basic

    # Container für Telemetrie
    container_id = api.get_or_create_container(token, CONTAINER_NAME, api.CONTAINER_KEYS)
    if container_id:
        tel = api.get_telematic_data(token, VIN, container_id)
        result["telemetry"] = tel
        # Telemetrie in DB speichern
        for key, entry in tel.items():
            if isinstance(entry, dict):
                db.save_telemetry(key, entry.get("value",""),
                                  entry.get("unit",""), entry.get("timestamp",""))

    # Ladehistorie (letzte 90 Tage)
    sessions = api.get_charging_history(token, VIN, days=90)
    if sessions:
        inserted = db.save_charging_sessions(sessions)
        result["new_sessions"] = inserted

    # Reifen
    tyres = api.get_tyre_diagnosis(token, VIN)
    if tyres:
        result["tyres"] = tyres

    # Ladeorte
    lbcs = api.get_lbcs(token, VIN)
    result["chargingLocations"] = lbcs

    db.save_snapshot(result)
    _last_fetch = time.time()
    return result


# ── API Routes ───────────────────────────────────────────────

@app.get("/api/status")
def status():
    tokens = db.load_tokens_db()
    has_token = bool(tokens and tokens.get("access_token"))
    return {
        "authenticated": has_token,
        "last_fetch": _last_fetch,
        "vin": VIN
    }


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if body.get("password") == PASSWORD:
        response = JSONResponse({"ok": True})
        response.set_cookie("auth_token", PASSWORD, httponly=True, samesite="lax")
        return response
    raise HTTPException(status_code=401, detail="Falsches Passwort")


@app.get("/api/snapshot")
def get_snapshot(request: Request):
    require_auth(request)
    snap = db.get_latest_snapshot()
    if not snap:
        return {"error": "no_data", "message": "Noch keine Daten — bitte zuerst abrufen"}
    return snap["data"]


@app.post("/api/fetch")
def trigger_fetch(request: Request):
    """Manuell Daten abrufen."""
    require_auth(request)
    with _fetch_lock:
        result = fetch_all_data()
    return result


@app.get("/api/charging")
def charging_history(request: Request, limit: int = 50, offset: int = 0):
    require_auth(request)
    sessions = db.get_charging_sessions(limit=limit, offset=offset)
    stats = db.get_charging_stats()
    return {"sessions": sessions, "stats": stats}


@app.get("/api/telemetry/{key}")
def telemetry_history(key: str, request: Request, limit: int = 100):
    require_auth(request)
    full_key = key if "." in key else f"vehicle.{key}"
    history = db.get_telemetry_history(full_key, limit=limit)
    return {"key": full_key, "data": history}


# ── BMW Auth Routes ──────────────────────────────────────────

@app.post("/api/bmw/auth/start")
def bmw_auth_start(request: Request):
    """Startet BMW Device Code Flow."""
    require_auth(request)
    dcf = api.get_device_code(CLIENT_ID)
    if not dcf:
        raise HTTPException(500, "Device Code Flow fehlgeschlagen")
    _dcf_state.update(dcf)
    return {
        "user_code": dcf["user_code"],
        "verification_uri": dcf["verification_uri"],
        "login_url": f"{dcf['verification_uri']}?user_code={dcf['user_code']}",
        "expires_in": dcf.get("expires_in", 300)
    }


@app.post("/api/bmw/auth/poll")
def bmw_auth_poll(request: Request):
    """Pollt einmal auf BMW Token."""
    require_auth(request)
    if not _dcf_state.get("device_code"):
        raise HTTPException(400, "Kein aktiver Auth-Flow")
    tokens = api.poll_token(CLIENT_ID, _dcf_state["device_code"], _dcf_state["code_verifier"])
    if tokens:
        db.save_tokens_db(tokens)
        api._token_cache = tokens
        _dcf_state.clear()
        return {"ok": True, "gcid": tokens.get("gcid")}
    return {"ok": False, "pending": True}


@app.get("/api/bmw/containers")
def list_containers(request: Request):
    require_auth(request)
    tokens = db.load_tokens_db()
    token = api.get_valid_token(CLIENT_ID, tokens)
    if not token:
        raise HTTPException(401, "Kein BMW Token")
    return api.get_containers(token)


# ── Static Files & SPA ───────────────────────────────────────

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def serve_spa(full_path: str, request: Request):
    index = static_dir / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse("<h1>BMW Dashboard</h1><p>static/index.html fehlt</p>")


# ── Startup ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    print(f"BMW Dashboard gestartet — VIN: {VIN}")
    tokens = db.load_tokens_db()
    if tokens:
        print("  ✓ BMW Tokens vorhanden")
    else:
        print("  ⚠ Keine BMW Tokens — bitte über Dashboard einloggen")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
