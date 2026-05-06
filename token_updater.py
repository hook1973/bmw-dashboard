"""
BMW Token Updater — Render Cron Job
Alle sensitiven Werte kommen aus Environment Variables — nie im Code!
"""
import os, sys, time
import requests

RENDER_API_KEY    = os.getenv("RENDER_API_KEY", "")
SERVICE_ID        = os.getenv("RENDER_SERVICE_ID", "")
BMW_CLIENT_ID     = os.getenv("BMW_CLIENT_ID", "5f4b2906-4dc0-4874-88c6-c84accdcf284")
BMW_REFRESH_TOKEN = os.getenv("BMW_REFRESH_TOKEN", "")
BMW_GCID          = os.getenv("BMW_GCID", "")

BASE_AUTH  = "https://customer.bmwgroup.com"
RENDER_API = "https://api.render.com/v1"

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def get_new_token():
    r = requests.post(f"{BASE_AUTH}/gcdm/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": BMW_REFRESH_TOKEN,
              "client_id": BMW_CLIENT_ID}, timeout=15)
    if r.status_code == 200:
        return r.json()
    log(f"✗ Token fehlgeschlagen: {r.status_code}")
    return None

def update_env(key, value):
    r = requests.get(f"{RENDER_API}/services/{SERVICE_ID}/env-vars",
        headers={"Authorization": f"Bearer {RENDER_API_KEY}", "Accept": "application/json"}, timeout=15)
    if r.status_code != 200: return False
    raw = r.json()
    env_vars = [item.get("envVar", item) for item in raw if isinstance(item, dict)]
    updated = []
    found = False
    for var in env_vars:
        k = var.get("key","")
        if k == key:
            updated.append({"key": key, "value": value}); found = True
        elif k:
            updated.append({"key": k, "value": var.get("value","")})
    if not found:
        updated.append({"key": key, "value": value})
    r2 = requests.put(f"{RENDER_API}/services/{SERVICE_ID}/env-vars",
        headers={"Authorization": f"Bearer {RENDER_API_KEY}",
                 "Accept": "application/json", "Content-Type": "application/json"},
        json=updated, timeout=15)
    return r2.status_code in (200, 201)

def restart():
    r = requests.post(f"{RENDER_API}/services/{SERVICE_ID}/restart",
        headers={"Authorization": f"Bearer {RENDER_API_KEY}", "Accept": "application/json"}, timeout=15)
    return r.status_code in (200, 201, 202)

def main():
    log("BMW Token Updater")
    if not RENDER_API_KEY or not SERVICE_ID:
        log("✗ RENDER_API_KEY oder RENDER_SERVICE_ID fehlt!"); sys.exit(1)
    if not BMW_REFRESH_TOKEN:
        log("✗ BMW_REFRESH_TOKEN fehlt!"); sys.exit(1)
    tokens = get_new_token()
    if not tokens: sys.exit(1)
    ok = update_env("BMW_ACCESS_TOKEN", tokens["access_token"])
    if tokens.get("refresh_token") != BMW_REFRESH_TOKEN:
        update_env("BMW_REFRESH_TOKEN", tokens["refresh_token"])
    if ok:
        time.sleep(2); restart()
        log("✓ Fertig")
    else:
        log("✗ ENV Update fehlgeschlagen"); sys.exit(1)

if __name__ == "__main__":
    main()
