# Campsite Booking — MVP

A California-first campsite search & alerts MVP.

Structure:
- `web/` — Next.js frontend (minimal)
- `backend/` — FastAPI backend with mock data for search and alerts

Quick start (requires Node.js and Python 3.10+):

1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

2. Frontend

```bash
cd web
npm install
npm run dev
```

Open the frontend at http://localhost:3000 and the backend at http://localhost:8000.

Next steps: connect RIDB and NPS APIs, add ReserveCalifornia integration strategy, implement persistent DB and notifications.
