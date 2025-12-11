# AirRide Backend — Flask + SSE + TomTom

## 🚀 Deploy su Render
1. Carica la repo su GitHub
2. Vai su https://dashboard.render.com/
3. New → Web Service
4. Collega il repo
5. Imposta:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --workers 1 --timeout 120 --bind 0.0.0.0:$PORT`

## 🔧 Endpoints
- `POST /update_position`
- `POST /complete_trip`
- `GET /route_info`
- `GET /stream` (SSE)

## ⚠️ Note
Il backend non usa Firebase: tutte le operazioni Firestore vengono fatte sul frontend.
