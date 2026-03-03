from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os
import random
from datetime import datetime, timedelta

app = FastAPI()

# -----------------------------
# CORS (important for your HTML)
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# CONFIG
# -----------------------------
CASUAL_RULES = {
    "min_ev": 0.01,
    "min_confirms": 0,
    "require_pinnacle": False,
}

SHARP_RULES = {
    "min_ev": 0.03,
    "min_confirms": 1,
    "require_pinnacle": True,
}

MIN_DAILY_PICKS = 3


# -----------------------------
# DEBUG HELPER
# -----------------------------
def debug(stage, items):
    try:
        print(f"[SCAN DEBUG] {stage}: {len(items)}")
    except Exception:
        pass


# -----------------------------
# MOCK ODDS DATA (safe starter)
# Replace later with real Odds API
# -----------------------------
def get_mock_events():
    teams = [
        ("Lakers", "Warriors"),
        ("Celtics", "Heat"),
        ("Bucks", "Knicks"),
        ("Suns", "Mavericks"),
        ("Nuggets", "Clippers"),
    ]

    events = []
    now = datetime.utcnow()

    for i, (home, away) in enumerate(teams):
        events.append({
            "id": i + 1,
            "sport": "basketball_nba",
            "home_team": home,
            "away_team": away,
            "commence_time": (now + timedelta(hours=6 + i)).isoformat(),
            "ev": random.uniform(-0.02, 0.06),
            "confirms": random.randint(0, 2),
            "has_pinnacle": random.choice([True, False]),
            "odds": random.choice([-120, -110, +105, +120]),
        })

    return events


# -----------------------------
# FALLBACK PICKS (prevents empty UX)
# -----------------------------
def generate_fallback_picks(events):
    leans = []

    for e in events:
        if e.get("ev", 0) > 0:
            pick = {
                "game": f"{e['away_team']} @ {e['home_team']}",
                "sport": e["sport"],
                "pick": e["home_team"],
                "confidence": 0.52,
                "tier": "lean",
                "start_time": e["commence_time"],
                "odds": e["odds"],
            }
            leans.append(pick)

    return sorted(leans, key=lambda x: -x["confidence"])


# -----------------------------
# MAIN SCAN ENDPOINT
# -----------------------------
@app.get("/scan")
async def scan(request: Request):
    mode = request.query_params.get("mode", "casual")
    rules = SHARP_RULES if mode == "sharp" else CASUAL_RULES

    # -----------------------------
    # STEP 1 — get events
    # -----------------------------
    events = get_mock_events()
    debug("raw_events", events)

    picks = []

    # -----------------------------
    # STEP 2 — filter events
    # -----------------------------
    for e in events:
        ev = e.get("ev", 0)
        confirms = e.get("confirms", 0)
        has_pinnacle = e.get("has_pinnacle", False)

        # EV filter
        if ev < rules["min_ev"]:
            continue

        # confirms filter
        if confirms < rules["min_confirms"]:
            continue

        # pinnacle filter
        if rules["require_pinnacle"] and not has_pinnacle:
            continue

        pick = {
            "game": f"{e['away_team']} @ {e['home_team']}",
            "sport": e["sport"],
            "pick": e["home_team"],
            "confidence": round(0.55 + ev, 3),
            "tier": "sharp" if mode == "sharp" else "value",
            "start_time": e["commence_time"],
            "odds": e["odds"],
        }

        picks.append(pick)

    debug("after_filters", picks)

    # -----------------------------
    # STEP 3 — guarantee casual picks
    # -----------------------------
    if mode == "casual" and len(picks) < MIN_DAILY_PICKS:
        fallback = generate_fallback_picks(events)
        needed = MIN_DAILY_PICKS - len(picks)
        picks.extend(fallback[:needed])

    debug("final", picks)

    return {
        "mode": mode,
        "count": len(picks),
        "picks": picks,
        "generated_at": datetime.utcnow().isoformat(),
    }


# -----------------------------
# HEALTH CHECK (for Render)
# -----------------------------
@app.get("/")
async def root():
    return {"status": "AlgoBets AI backend running"}