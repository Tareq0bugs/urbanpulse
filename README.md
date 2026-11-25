# UrbanPulse — Smart City Intelligence and Response System

Flask + SQLite + vanilla HTML/CSS/JS. No Node, no frameworks, no CDN (works fully offline).

## Run
```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```
The database `urbanpulse.db` is created and seeded automatically on first run. Data persists between restarts.
Use "Reset sample data" (page footer) to restore the demo data.

## Structure
- `app.py` — Flask API, SQLite, local analysis engine, scheduler/allocation, simulation
- `static/index.html, style.css, app.js` — Arabic (RTL) / English UI

## The AI engine (simulated)
`analyze()` in `app.py` is a **local rule-based engine** (keyword classification, weighted scoring, similarity detection),
not a live AI model. Priority (0-100) = 0.30 safety + 0.20 people affected + 0.20 infrastructure damage + 0.15 urgency
+ 0.15 community impact, +6 for duplicates. Levels: ≥75 critical, ≥55 high, ≥35 medium, else low.
To connect a real model, replace the body of `analyze()` and keep the same returned dict
(`category, priority, level, team, urgency_h, analysis{...}`).

## API
`GET/POST /api/reports`, `GET/PUT/DELETE /api/reports/<id>`, `POST /api/analyze`, `GET /api/stats`, `GET /api/resources`,
`POST /api/allocate {commit}`, `POST /api/simulate {scenario, apply}`, `POST /api/reset`.

## Demo flow (competition)
Click "Demo mode" -> "Run demo": submits a report, shows analysis, score, duplicate detection, recommended team,
allocation, an emergency simulation, and updated city stats.
