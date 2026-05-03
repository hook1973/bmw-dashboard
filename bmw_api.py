"""
BMW CarData API Client
"""
import base64, hashlib, json, secrets, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

BASE_AUTH = "https://customer.bmwgroup.com"
BASE_API  = "https://api-cardata.bmwgroup.com"
SCOPES    = "authenticate_user openid cardata:api:read cardata:streaming:read"

# Telemetrie-Keys für den Container
# Diese werden nach erfolgreichem Key-Test befüllt
# Nur bestätigte gültige Keys (getestet 02.05.2026)
CONTAINER_KEYS = [
    "vehicle.vehicle.travelledDistance",
    "vehicle.powertrain.electric.battery.stateOfCharge.displayed",
    "vehicle.cabin.infotainment.navigation.currentLocation.latitude",
    "vehicle.cabin.infotainment.navigation.currentLocation.longitude",
    "vehicle.cabin.infotainment.navigation.currentLocation.heading",
    "vehicle.cabin.infotainment.navigation.currentLocation.altitude",
    "vehicle.body.trunk.isOpen",
    "vehicle.body.trunk.isLocked",
    "vehicle.body.hood.isOpen",
    "vehicle.cabin.door.row1.driver.isOpen",
]

_token_cache = {}


def hdrs(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-version": "v1"
    }


def generate_pkce():
    v = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def refresh_token(client_id: str, refresh_tok: str) -> dict | None:
    r = requests.post(f"{BASE_AUTH}/gcdm/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_tok,
              "client_id": client_id}, timeout=15)
    if r.status_code == 200:
        t = r.json()
        t["saved_at"] = time.time()
        return t
    return None


def get_valid_token(client_id: str, stored_tokens: dict | None) -> str | None:
    """Gibt gültigen Access Token zurück, erneuert wenn nötig."""
    global _token_cache
    if _token_cache:
        age = time.time() - _token_cache.get("saved_at", 0)
        if age < _token_cache.get("expires_in", 3600) - 60:
            return _token_cache["access_token"]

    if stored_tokens:
        age = time.time() - stored_tokens.get("saved_at", 0)
        if age < stored_tokens.get("expires_in", 3600) - 60:
            _token_cache = stored_tokens
            return stored_tokens["access_token"]
        if age < 1_200_000 and stored_tokens.get("refresh_token"):
            new_t = refresh_token(client_id, stored_tokens["refresh_token"])
            if new_t:
                _token_cache = new_t
                return new_t["access_token"]
    return None


def get_device_code(client_id: str) -> dict | None:
    """Startet Device Code Flow — gibt user_code und device_code zurück."""
    verifier, challenge = generate_pkce()
    r = requests.post(f"{BASE_AUTH}/gcdm/oauth/device/code",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"client_id": client_id, "response_type": "device_code",
              "scope": SCOPES, "code_challenge": challenge,
              "code_challenge_method": "S256"}, timeout=15)
    if r.status_code == 200:
        d = r.json()
        d["code_verifier"] = verifier
        return d
    return None


def poll_token(client_id: str, device_code: str, verifier: str) -> dict | None:
    """Pollt einmal auf Token — gibt Tokens zurück oder None."""
    r = requests.post(f"{BASE_AUTH}/gcdm/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"client_id": client_id, "device_code": device_code,
              "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
              "code_verifier": verifier}, timeout=15)
    if r.status_code == 200:
        t = r.json()
        t["saved_at"] = time.time()
        return t
    return None


# ── API Calls ────────────────────────────────────────────────

def get_basic_data(token: str, vin: str) -> dict | None:
    r = requests.get(f"{BASE_API}/customers/vehicles/{vin}/basicData",
        headers=hdrs(token), timeout=15)
    print(f"    basicData status: {r.status_code}, body: {r.text[:200]}")
    return r.json() if r.status_code == 200 else None


def get_charging_history(token: str, vin: str, days: int = 90) -> list:
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to  = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(f"{BASE_API}/customers/vehicles/{vin}/chargingHistory",
        headers=hdrs(token), params={"from": frm, "to": to}, timeout=15)
    if r.status_code == 200:
        return r.json().get("data", [])
    return []


def get_tyre_diagnosis(token: str, vin: str) -> dict | None:
    r = requests.get(f"{BASE_API}/customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis",
        headers=hdrs(token), timeout=15)
    return r.json() if r.status_code == 200 else None


def get_lbcs(token: str, vin: str) -> list:
    r = requests.get(f"{BASE_API}/customers/vehicles/{vin}/locationBasedChargingSettings",
        headers=hdrs(token), timeout=15)
    if r.status_code == 200:
        return r.json().get("data", [])
    return []


def get_mappings(token: str) -> list:
    r = requests.get(f"{BASE_API}/customers/vehicles/mappings",
        headers=hdrs(token), timeout=15)
    return r.json() if r.status_code == 200 else []


def get_containers(token: str) -> list:
    r = requests.get(f"{BASE_API}/customers/containers",
        headers=hdrs(token), timeout=15)
    return r.json().get("containers", []) if r.status_code == 200 else []


def create_container(token: str, name: str, keys: list) -> str | None:
    r = requests.post(f"{BASE_API}/customers/containers",
        headers=hdrs(token),
        json={"name": name, "purpose": "dashboard", "technicalDescriptors": keys}, timeout=15)
    if r.status_code == 201:
        return r.json().get("containerId")
    return None


def delete_container(token: str, container_id: str) -> bool:
    r = requests.delete(f"{BASE_API}/customers/containers/{container_id}",
        headers=hdrs(token), timeout=15)
    return r.status_code == 204


def get_telematic_data(token: str, vin: str, container_id: str) -> dict:
    r = requests.get(f"{BASE_API}/customers/vehicles/{vin}/telematicData",
        headers=hdrs(token), params={"containerId": container_id}, timeout=15)
    if r.status_code == 200:
        return r.json().get("telematicData", {})
    return {}


def get_or_create_container(token: str, name: str, keys: list) -> str | None:
    """Holt bestehenden Container oder erstellt neuen."""
    containers = get_containers(token)
    print(f"    Vorhandene Container: {[c.get('name') for c in containers]}")
    for c in containers:
        if c.get("name") == name and c.get("state") == "ACTIVE":
            print(f"    Container gefunden: {c['containerId']}")
            return c["containerId"]
    print(f"    Erstelle neuen Container '{name}'...")
    cid = create_container(token, name, keys)
    print(f"    Container erstellt: {cid}")
    return cid
