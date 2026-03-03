import os
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# =====================================================
# App Setup
# =====================================================

app = FastAPI(title="AlgoBets AI API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# =====================================================
# Config
# =====================================================

MIN_EDGE = 0.02
MAX_GAME_HOURS_AHEAD = 36  # 🚨 prevents stale far-future games
REQUEST_TIMEOUT = 10

# =====================================================
# Health Endpoints
# =====================================================

@app.get("/")
def root():
    return {
        "status": "AlgoBets AI live",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/health")
def health():
    return {"ok": True}

# =====================================================
# Time Helpers
# =====================================================

def parse_game_time(commence_time: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except Exception:
        return None

def is_future_game(commence_time: str) -> bool:
    """Only allow games that have not started."""
    game_time = parse_game_time(commence_time)
    if not game_time:
        return False
    return game_time > datetime.now(timezone.utc)

def within_time_window(commence_time: str) -> bool:
    """Only allow reasonably near-term games."""
    game_time = parse_game_time(commence_time)
    if not game_time:
        return False
    now = datetime.now(timezone.utc)
    return now < game_time < now + timedelta(hours=MAX_GAME_HOURS_AHEAD)

# =====================================================
# Odds Math
# =====================================================

def american_to_prob(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)

def calculate_edge(model_prob: float, market_odds: int) -> float:
    market_prob = american_to_prob(market_odds)
    return model_prob - market_prob

# =====================================================
# Odds Fetch
# =====================================================

def fetch_odds_safe() -> Optional[List[Dict[str, Any]]]:
    """Fetch real odds. Returns None if unavailable."""

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

        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()

    except Exception as e:
        print("Odds API error:", e)
        return None

# =====================================================
# Mock Model (fallback)
# =====================================================

def run_mock_model() -> List[Dict[str, Any]]:
    """Used if odds API unavailable."""

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

        if edge > MIN_EDGE:
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

# =====================================================
# Real Odds Processor
# =====================================================

def process_real_odds(odds_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []

    for game in odds_data:

        commence_time = game.get("commence_time")

        # 🚨 CRITICAL FILTERS
        if not commence_time:
            continue

        if not is_future_game(commence_time):
            continue

        if not within_time_window(commence_time):
            continue

        home = game.get("home_team")
        away = game.get("away_team")

        # ---- placeholder model ----
        model_prob = random.uniform(0.52, 0.60)
        market_odds = -110
        edge = calculate_edge(model_prob, market_odds)

        if edge > MIN_EDGE:
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

# =====================================================
# MAIN ENDPOINT (YOUR FRONTEND USES THIS)
# =====================================================

@app.get("/api/picks")
def get_picks():
    odds_data = fetch_odds_safe()

    if odds_data:
        picks = process_real_odds(odds_data)
    else:
        picks = run_mock_model()

    return picks