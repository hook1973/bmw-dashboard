# BMW i4 Dashboard

Live-Dashboard für BMW i4 eDrive40 via BMW CarData API.

## Features
- Akkustand, Reichweite, Ladestatus (live)
- Komplette Ladehistorie mit Statistiken
- Akku-Gesundheits-Kennzahlen
- GPS-Position (wenn verfügbar)
- Reifenzustand
- MQTT Streaming (geplant)

## Setup

### 1. Lokal testen
```bash
pip install -r requirements.txt
python main.py
# → http://localhost:8000
```

### 2. Auf Render.com deployen
1. Repository zu GitHub pushen
2. Render.com → New Web Service → GitHub Repo
3. Environment Variables setzen:
   - `BMW_CLIENT_ID` = deine Client ID
   - `BMW_VIN` = deine VIN
   - `DASHBOARD_PASSWORD` = dein Passwort
4. Deploy

### 3. BMW CarData verbinden
1. Dashboard öffnen → einloggen
2. "BMW verbinden" klicken
3. BMW ConnectedDrive Login durchführen
4. "Abrufen" klicken

## BMW CarData
- Client ID: im CarData Customer Portal unter "Create CarData Client"
- Rate Limit: 50 Requests/Tag (REST API)
- Streaming: MQTT, kein Rate Limit (in Entwicklung)
