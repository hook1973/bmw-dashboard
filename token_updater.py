"""
BMW Token Updater — Render Cron Job
====================================
Läuft täglich automatisch auf Render.
1. Holt neuen Access Token via Refresh Token
2. Aktualisiert BMW_ACCESS_TOKEN in Render ENV
3. Startet Dashboard-Service neu

Render Cron Job Einstellungen:
  Build Command: pip install requests
  Start Command: python token_updater.py
  Schedule:      0 5 * * *  (täglich 05:00 UTC = 06:00 Wien)
"""
import os
import sys
import time
import requests

# ── Konfiguration ────────────────────────────────────────────
RENDER_API_KEY    = os.getenv("RENDER_API_KEY", "rnd_3D8MtyBbG70Z0e4hAbKsxPP0M3db")
SERVICE_ID        = os.getenv("RENDER_SERVICE_ID", "srv-d7qcu89kh4rs73b84tvg")
BMW_CLIENT_ID     = os.getenv("BMW_CLIENT_ID", "5f4b2906-4dc0-4874-88c6-c84accdcf284")
BMW_REFRESH_TOKEN = os.getenv("BMW_REFRESH_TOKEN", "")
BMW_GCID          = os.getenv("BMW_GCID", "")

BASE_AUTH    = "https://customer.bmwgroup.com"
RENDER_API   = "https://api.render.com/v1"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_new_access_token() -> dict | None:
    """Holt neuen Access Token via Refresh Token."""
    log("→ Erneuere BMW Access Token...")
    r = requests.post(
        f"{BASE_AUTH}/gcdm/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "refresh_token": BMW_REFRESH_TOKEN,
            "client_id":     BMW_CLIENT_ID,
        },
        timeout=15
    )
    if r.status_code == 200:
        tokens = r.json()
        log(f"  ✓ Neuer Access Token erhalten (GCID: {tokens.get('gcid','?')})")
        return tokens
    log(f"  ✗ Token-Erneuerung fehlgeschlagen: {r.status_code} — {r.text[:200]}")
    return None


def update_render_env(key: str, value: str) -> bool:
    """Aktualisiert eine Environment Variable auf Render."""
    log(f"→ Aktualisiere Render ENV: {key}...")
    
    # Erst alle ENV Vars abrufen
    r = requests.get(
        f"{RENDER_API}/services/{SERVICE_ID}/env-vars",
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
        },
        timeout=15
    )
    
    if r.status_code != 200:
        log(f"  ✗ ENV Vars abrufen fehlgeschlagen: {r.status_code} — {r.text[:200]}")
        return False
    
    env_vars = r.json()
    
    # Bestehende Vars als Liste aufbereiten
    updated = []
    found = False
    for var in env_vars:
        if var.get("key") == key:
            updated.append({"key": key, "value": value})
            found = True
        else:
            updated.append({"key": var["key"], "value": var.get("value", "")})
    
    if not found:
        updated.append({"key": key, "value": value})
    
    # Alle ENV Vars auf einmal aktualisieren
    r2 = requests.put(
        f"{RENDER_API}/services/{SERVICE_ID}/env-vars",
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=updated,
        timeout=15
    )
    
    if r2.status_code in (200, 201):
        log(f"  ✓ ENV Variable {key} aktualisiert")
        return True
    
    log(f"  ✗ ENV Update fehlgeschlagen: {r2.status_code} — {r2.text[:200]}")
    return False


def restart_service() -> bool:
    """Startet den Dashboard-Service neu."""
    log("→ Starte Dashboard-Service neu...")
    r = requests.post(
        f"{RENDER_API}/services/{SERVICE_ID}/restart",
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
        },
        timeout=15
    )
    if r.status_code in (200, 201, 202):
        log("  ✓ Service neugestartet")
        return True
    log(f"  ✗ Neustart fehlgeschlagen: {r.status_code} — {r.text[:200]}")
    return False


def main():
    log("=" * 50)
    log("  BMW Token Updater gestartet")
    log("=" * 50)

    if not BMW_REFRESH_TOKEN:
        log("✗ BMW_REFRESH_TOKEN nicht gesetzt!")
        sys.exit(1)

    # 1. Neuen Token holen
    tokens = get_new_access_token()
    if not tokens:
        log("✗ Konnte keinen neuen Token holen — abbruch")
        sys.exit(1)

    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    # 2. Render ENV aktualisieren
    ok1 = update_render_env("BMW_ACCESS_TOKEN", access_token)
    
    # Auch den Refresh Token aktualisieren (wird bei jedem Refresh erneuert)
    if refresh_token and refresh_token != BMW_REFRESH_TOKEN:
        log("  → Refresh Token hat sich geändert — aktualisiere auch BMW_REFRESH_TOKEN")
        update_render_env("BMW_REFRESH_TOKEN", refresh_token)

    if not ok1:
        log("✗ ENV Update fehlgeschlagen")
        sys.exit(1)

    # 3. Service neu starten
    time.sleep(2)
    restart_service()

    log("")
    log("=" * 50)
    log("  ✓ Token Update abgeschlossen!")
    log("=" * 50)


if __name__ == "__main__":
    main()
