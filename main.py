import os
import random
from datetime import datetime, timezone

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# =========================
# App Setup
# =========================

app = FastAPI(title="AlgoBets AI API", version="1.0")

# --- CORS (required for your frontend) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later if you want
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# Environment
# =========================

ODDS_API_KEY = os.getenv("ODDS_API_KEY")  # set on Render later

# =========================
# Health Checks
# =========================

@app.get("/")
def root():
    return {
        "status": "AlgoBets AI live",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/health")
def health():
    return {"ok": True}

# =========================
# Helpers
# =========================

def american_to_prob(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)

def calculate_edge(model_prob: float, market_odds: int) -> float:
    """Model edge vs market."""
    market_prob = american_to_prob(market_odds)
    return model_prob - market_prob

# =========================
# Odds Fetch (safe fallback)
# =========================

def fetch_odds_safe():
    """
    Attempts real odds pull.
    Falls back to mock data if API key missing.
    """

    if not ODDS_API_KEY:
        return None

    try:
        url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "us",
            "markets": "spreads",
            "oddsFormat": "american",
        }

        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    except Exception as e:
        print("Odds API error:", e)
        return None

# =========================
# Mock Model (replace later)
# =========================

def run_mock_model():
    """Temporary model until your real one is wired."""
    games = [
        ("Lakers", "Warriors"),
        ("Celtics", "Heat"),
        ("Bucks", "Knicks"),
    ]

    picks = []

    for home, away in games:
        model_prob = random.uniform(0.52, 0.60)
        market_odds = -110
        edge = calculate_edge(model_prob, market_odds)

        # only return +EV picks
        if edge > 0.02:
            picks.append({
                "home_team": home,
                "away_team": away,
                "pick": f"{away} +3.5",
                "edge": round(edge, 4),
                "confidence": round(model_prob, 4),
                "odds": market_odds,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return picks

# =========================
# MAIN PICKS ENDPOINT (CRITICAL)
# =========================

@app.get("/api/picks")
def get_picks():
    """
    Primary endpoint used by your frontend.
    Always returns a JSON list.
    """

    # try real odds first (future ready)
    odds_data = fetch_odds_safe()

    # TODO later: plug real model here using odds_data

    # for now use stable mock model
    picks = run_mock_model()

    return picks