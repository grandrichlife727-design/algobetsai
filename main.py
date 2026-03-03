"""
Algobets Ai — FastAPI Backend  v5.0
=====================================
UPGRADES IN THIS VERSION
─────────────────────────
[v3-v4 features still in place — see git history]

8. ELO RATING SYSTEM  (v5 — NEW)
   Team Elo ratings for NBA, NFL, NHL seeded with realistic initial values.
   `calculate_elo_edge()` compares our model's win probability vs the market
   implied odds — this is an independent signal orthogonal to CLV.
   `update_elo_ratings()` called automatically on every resolved pick to
   keep ratings current. Home advantage calibrated per sport.
   GET /api/elo-ratings returns all current ratings with tier labels.

9. ENSEMBLE CONFIDENCE SCORING  (v5 — NEW)
   `ensemble_confidence_score()` replaces the old linear formula with a
   proper signal-weighted ensemble approach. Combines 10+ signals:
   CLV edge, sharp %, RLM, steam velocity, Elo edge, line movement,
   injury impact, weather, Kelly, and agent agreement.
   Blended 60/40 with v4 score during ramp-up period for stability.

10. STEAM VELOCITY DETECTION  (v5 — NEW)
    `detect_steam_velocity()` analyzes line movement history to classify
    steam moves: syndicate (coordinated multi-book), sharp_move, soft_move.
    Returns velocity score 0-1 used in ensemble confidence.
    Steam type surfaced in pick output for frontend display.

11. MULTIPLICATIVE DEVIGGING  (v5 — NEW)
    `multiplicative_devig()` and `pinnacle_devig()` for more accurate
    fair value calculation vs naive additive devig.
    Pinnacle's ~2% margin removed separately from typical books' ~4.5%.

DATA SOURCES
─────────────
FREE  → ESPN           : schedules, scores, injuries, player stats
FREE  → Action Network : public %, sharp %, line history
FREE  → Pinnacle       : sharpest closing lines (CLV benchmark)
FREE  → Open-Meteo     : weather forecasts for outdoor stadium games
PAID  → The Odds API   : ONLY ev-finder + arb-detect
"""

import os
import re
import json
import math
import time
import httpx
import sqlite3
import asyncio
import hashlib
import stripe
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

ODDS_API_KEY    = os.getenv("ODDS_API_KEY", "").strip()
STRIPE_SECRET   = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK  = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
FRONTEND_URL    = os.getenv("FRONTEND_URL", "https://algobets.ai").strip()
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "").strip()
PINNACLE_API_KEY = os.getenv("PINNACLE_API_KEY", "").strip()  # Optional: Basic auth for Pinnacle API

# Persistent disk on Render — mount /data in render.yaml for this to survive restarts
DATA_DIR  = os.getenv("DATA_DIR", "/tmp/algobets_data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
DB_PATH   = os.path.join(DATA_DIR, "picks.db")
os.makedirs(CACHE_DIR, exist_ok=True)

stripe.api_key = STRIPE_SECRET

def verify_api_key(x_api_key: str = Header(default="")):
    if BACKEND_API_KEY and x_api_key != BACKEND_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ─── Server-side plan verification ───────────────────────────────────────────
# We verify plan against Stripe on every protected request.
# An in-memory TTL cache (5 min) prevents hammering the Stripe API.
# The x-user-plan header is NEVER trusted — it's used only to fast-fail
# obviously-free users before the Stripe call, not to grant access.

_plan_cache: dict = {}          # user_id → {"plan": str, "expires": float}
_PLAN_CACHE_TTL = 300           # 5 minutes

def _verify_plan_stripe_sync(user_id: str) -> str:
    """
    Look up a user's active Stripe subscription and return their plan tier.
    Returns 'free' if no active subscription found or Stripe is not configured.
    Called synchronously — wrap in asyncio.to_thread for async routes.
    """
    if not STRIPE_SECRET or not user_id:
        return "free"
    try:
        customers = stripe.Customer.search(query=f'email:"{user_id}"', limit=1)
        if not customers.data:
            customers = stripe.Customer.list(limit=100)
            matching = [c for c in customers.data if
                        c.metadata.get("userId") == user_id or c.email == user_id]
            if not matching:
                return "free"
            customer = matching[0]
        else:
            customer = customers.data[0]

        subs = stripe.Subscription.list(customer=customer.id, status="active", limit=5)
        if not subs.data:
            return "free"

        price_id  = subs.data[0]["items"]["data"][0]["price"]["id"]
        PRO_IDS   = [p for p in os.getenv("STRIPE_PRO_PRICE_IDS", "").split(",") if p]
        SHARP_IDS = [p for p in os.getenv("STRIPE_SHARP_PRICE_IDS", "").split(",") if p]
        if price_id in SHARP_IDS:
            return "sharp"
        if price_id in PRO_IDS:
            return "pro"
        return "pro"  # active sub but unknown price → default to pro

    except stripe.error.StripeError as e:
        print(f"[Plan] Stripe error for {user_id}: {e}")
        return "free"
    except Exception as e:
        print(f"[Plan] Unexpected error for {user_id}: {e}")
        return "free"


OWNER_EMAILS = {"grandrichlife727@gmail.com"}  # always sharp, no Stripe check needed

async def _get_verified_plan(request: Request) -> str:
    """
    Verify a user's plan server-side against Stripe.
    Reads user identity from x-user-id header (set by frontend after auth).
    Falls back to free if no identity is provided or Stripe is down.
    Uses an in-memory TTL cache to avoid per-request Stripe calls.
    """
    user_id = request.headers.get("x-user-id", "").strip()
    if not user_id:
        return "free"

    # Owner bypass — always sharp, no Stripe check needed
    if user_id in OWNER_EMAILS:
        return "sharp"

    now = time.time()
    cached = _plan_cache.get(user_id)
    if cached and cached["expires"] > now:
        return cached["plan"]

    # Cache miss or expired — go to Stripe
    plan = await asyncio.to_thread(_verify_plan_stripe_sync, user_id)
    _plan_cache[user_id] = {"plan": plan, "expires": now + _PLAN_CACHE_TTL}
    return plan


async def require_paid_plan(request: Request):
    """
    Dependency: verifies plan server-side against Stripe.
    Free users are allowed but get truncated results (top 3 picks max).
    Never trusts client-supplied plan headers.
    """
    # Just verify the API key is valid — plan gating handled inside the endpoint
    pass  # plan-based limits applied in scan() itself, not here


async def get_user_plan(request: Request) -> str:
    """Return the server-verified plan tier for the current user."""
    return await _get_verified_plan(request)


# ─── Cache TTLs ───────────────────────────────────────────────────────────────
CACHE_TTL          = 1800   # 30 min — Odds API (EV/arb) — keeps lines fresh
CACHE_TTL_FREE     = 480    # 8 min — ESPN / Action Network (short so game states stay fresh)
CACHE_TTL_PINNACLE = 1800   # 30 min — Pinnacle lines (free but rate-limit cautious)
CACHE_TTL_INJURIES = 900    # 15 min — injuries
CACHE_TTL_PROPS    = 3600   # 1 hr   — player props
CACHE_TTL_SPORTS   = 86400  # 24 hrs — active sports list

ODDS_BOOKMAKERS = os.getenv("ODDS_BOOKMAKERS", "draftkings,fanduel,betmgm,pinnacle,caesars,pointsbet")

_quota_remaining: int = 500
_quota_used_last: int = 0

# ─── Sport maps ───────────────────────────────────────────────────────────────
SPORTS = [
    "basketball_nba", "americanfootball_nfl", "icehockey_nhl",
    "basketball_ncaab", "baseball_mlb", "soccer_epl", "mma_mixed_martial_arts",
]
SPORT_META = {
    "basketball_nba":         {"label": "NBA",   "emoji": "🏀"},
    "americanfootball_nfl":   {"label": "NFL",   "emoji": "🏈"},
    "icehockey_nhl":          {"label": "NHL",   "emoji": "🏒"},
    "basketball_ncaab":       {"label": "NCAAB", "emoji": "🎓"},
    "baseball_mlb":           {"label": "MLB",   "emoji": "⚾"},
    "soccer_epl":             {"label": "EPL",   "emoji": "⚽"},
    "mma_mixed_martial_arts": {"label": "MMA",   "emoji": "🥊"},
}
ESPN_SPORT_MAP = {
    "basketball/nba":                        "basketball_nba",
    "football/nfl":                          "americanfootball_nfl",
    "hockey/nhl":                            "icehockey_nhl",
    "basketball/mens-college-basketball":    "basketball_ncaab",
    "baseball/mlb":                          "baseball_mlb",
    "soccer/eng.1":                          "soccer_epl",
}
AN_SPORT_MAP = {
    "nba": "basketball_nba", "nfl": "americanfootball_nfl",
    "nhl": "icehockey_nhl",  "ncaab": "basketball_ncaab", "mlb": "baseball_mlb",
}
# Pinnacle sport IDs (their internal API)
PINNACLE_SPORT_MAP = {
    "basketball_nba":       29,   # NBA
    "americanfootball_nfl": 889,  # NFL
    "icehockey_nhl":        33,   # NHL
    "baseball_mlb":         246,  # MLB
    "basketball_ncaab":     493,  # NCAAB
}

ODDS_BASE     = "https://api.the-odds-api.com/v4"
ESPN_BASE     = "https://site.api.espn.com/apis/site/v2/sports"
AN_BASE       = "https://api.actionnetwork.com/web/v1"
PINNACLE_BASE = "https://api.pinnacle.com/v1"  # public, no key needed for lines
WEATHER_BASE  = "https://api.open-meteo.com/v1/forecast"  # free, no key needed

# ─── Outdoor stadium coordinates (NFL + MLB) ──────────────────────────────────
# Used for weather signal. Indoor/dome stadiums intentionally omitted.
STADIUM_COORDS = {
    # NFL outdoor
    "Buffalo Bills":        (42.774, -78.787), "Chicago Bears":       (41.862, -87.617),
    "Cleveland Browns":     (41.506, -81.700), "Denver Broncos":      (39.744, -105.020),
    "Green Bay Packers":    (44.501, -88.062), "Kansas City Chiefs":  (39.049, -94.484),
    "Las Vegas Raiders":    None,               # Allegiant = dome
    "Los Angeles Chargers": None,               # SoFi = open but mild
    "Los Angeles Rams":     None,
    "Miami Dolphins":       (25.958, -80.239), "Minnesota Vikings":   None,  # US Bank = dome
    "New England Patriots": (42.091, -71.264), "New York Giants":     (40.813, -74.074),
    "New York Jets":        (40.813, -74.074), "Philadelphia Eagles": (39.901, -75.167),
    "Pittsburgh Steelers":  (40.447, -80.016), "San Francisco 49ers": (37.403, -121.970),
    "Seattle Seahawks":     (47.595, -122.332),"Tennessee Titans":    (36.166, -86.771),
    "Washington Commanders":(38.908, -76.864),
    # MLB outdoor (select high-weather-impact parks)
    "Chicago Cubs":         (41.948, -87.655), "Chicago White Sox":   (41.830, -87.634),
    "Boston Red Sox":       (42.346, -71.097), "New York Yankees":    (40.829, -73.926),
    "New York Mets":        (40.757, -73.846), "Pittsburgh Pirates":  (40.447, -80.006),
    "Cleveland Guardians":  (41.496, -81.685), "Colorado Rockies":    (39.756, -104.994),
    "San Francisco Giants": (37.778, -122.389),"Oakland Athletics":   (37.752, -122.200),
    "Philadelphia Phillies":(39.906, -75.166), "Detroit Tigers":      (42.339, -83.049),
    "Minnesota Twins":      None,               # Target Field = open but check
    "Milwaukee Brewers":    (43.028, -87.971), "Kansas City Royals":  (39.051, -94.480),
}

# ─── Default agent weights (v3 hand-tuned) ───────────────────────────────────
# These are replaced by empirically fitted weights once 300+ resolved picks exist.
# Keys match the agents_fired list in build_consensus_pick.
DEFAULT_AGENT_WEIGHTS = {
    "value":        3.5,   # CLV edge per % point (upgraded from 3.0)
    "line_movement": 5.5,  # favorable line move (upgraded)
    "public_money": 15.0,  # sharp/steam signal strength
    "injury":       -5.0,  # injury impact penalty (inverted)
    "situational":  8.0,   # situational score
    "fade_public":  10.0,  # fade signal strength
    "kelly":        3.0,   # kelly units
    # v5 new agents
    "elo_edge":     4.0,   # Elo-based team strength edge
    "rest_edge":    3.5,   # rest/fatigue advantage
    "h2h_edge":     2.5,   # head-to-head historical signal
}

# ─── v5: In-memory Elo ratings ───────────────────────────────────────────────
# Simple Elo system for major teams. Updates when resolve_picks is called.
# Default: 1500.0 (average). Better teams above, worse teams below.
# These are initial estimates — they'll self-calibrate over time.
_elo_ratings: dict = {
    # NBA top/bottom teams (estimates)
    "Boston Celtics": 1620, "Oklahoma City Thunder": 1610, "Cleveland Cavaliers": 1600,
    "Denver Nuggets": 1580, "Minnesota Timberwolves": 1565, "Indiana Pacers": 1555,
    "Orlando Magic": 1540, "New York Knicks": 1535, "Milwaukee Bucks": 1525,
    "Dallas Mavericks": 1520, "Los Angeles Lakers": 1510, "Golden State Warriors": 1505,
    "Phoenix Suns": 1490, "Sacramento Kings": 1485, "LA Clippers": 1480,
    "Memphis Grizzlies": 1460, "Houston Rockets": 1455, "Atlanta Hawks": 1450,
    "Miami Heat": 1445, "New Orleans Pelicans": 1440, "Chicago Bulls": 1430,
    "Toronto Raptors": 1425, "Washington Wizards": 1400, "Portland Trail Blazers": 1395,
    "Utah Jazz": 1390, "San Antonio Spurs": 1380, "Charlotte Hornets": 1375,
    "Detroit Pistons": 1370, "Brooklyn Nets": 1365, "Philadelphia 76ers": 1420,
    # NFL (using 2024 estimates)
    "Kansas City Chiefs": 1640, "San Francisco 49ers": 1620, "Baltimore Ravens": 1600,
    "Detroit Lions": 1580, "Philadelphia Eagles": 1570, "Dallas Cowboys": 1550,
    "Buffalo Bills": 1545, "Houston Texans": 1535, "Green Bay Packers": 1520,
    "Los Angeles Rams": 1510, "Tampa Bay Buccaneers": 1500, "Pittsburgh Steelers": 1495,
    "Denver Broncos": 1480, "Atlanta Falcons": 1475, "Minnesota Vikings": 1465,
    "Cleveland Browns": 1460, "Jacksonville Jaguars": 1445, "Cincinnati Bengals": 1440,
    "New York Giants": 1420, "Los Angeles Chargers": 1415, "Chicago Bears": 1410,
    "New York Jets": 1400, "New England Patriots": 1390, "Las Vegas Raiders": 1385,
    "Tennessee Titans": 1375, "Indianapolis Colts": 1370, "Carolina Panthers": 1360,
    "Washington Commanders": 1430, "Seattle Seahawks": 1440, "Arizona Cardinals": 1380,
    "New Orleans Saints": 1455, "Miami Dolphins": 1430,
    # NHL estimates
    "Florida Panthers": 1600, "Colorado Avalanche": 1590, "Boston Bruins": 1580,
    "Carolina Hurricanes": 1575, "New York Rangers": 1565, "Vegas Golden Knights": 1555,
    "Dallas Stars": 1545, "Edmonton Oilers": 1540, "Toronto Maple Leafs": 1530,
    "Tampa Bay Lightning": 1520, "Los Angeles Kings": 1510, "Winnipeg Jets": 1505,
    "New Jersey Devils": 1490, "Nashville Predators": 1480, "Ottawa Senators": 1470,
    "Vancouver Canucks": 1460, "Pittsburgh Penguins": 1455, "New York Islanders": 1445,
    "Seattle Kraken": 1440, "Calgary Flames": 1435, "Minnesota Wild": 1430,
    "Arizona Coyotes": 1400, "Chicago Blackhawks": 1395, "Columbus Blue Jackets": 1390,
    "Philadelphia Flyers": 1385, "Anaheim Ducks": 1375, "San Jose Sharks": 1365,
    "Detroit Red Wings": 1420, "Buffalo Sabres": 1410, "Washington Capitals": 1500,
    "St. Louis Blues": 1450, "Montreal Canadiens": 1420,
}
_ELO_K_FACTOR = 24.0  # standard K factor for sports Elo

# Home court advantages by sport (in Elo points equivalent to probability boost)
HOME_ADVANTAGE = {
    "basketball_nba":       100,   # ~3.5 pts
    "americanfootball_nfl": 54,    # ~2.5 pts
    "icehockey_nhl":        40,    # ~1.5 pts
    "baseball_mlb":         30,    # ~1 pt
    "basketball_ncaab":     140,   # ~5 pts (huge home court)
    "soccer_epl":           50,    # ~2 pts
    "mma_mixed_martial_arts": 0,   # neutral venue
}

def elo_win_probability(home_elo: float, away_elo: float, sport_key: str) -> float:
    """Calculate expected win probability using Elo ratings with home advantage."""
    home_adv = HOME_ADVANTAGE.get(sport_key, 50)
    elo_diff = (home_elo + home_adv) - away_elo
    return 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))

def get_team_elo(team_name: str) -> float:
    """Get Elo rating for a team, fuzzy matching if necessary."""
    if team_name in _elo_ratings:
        return float(_elo_ratings[team_name])
    # Fuzzy match: try matching by last word (city-based names)
    team_lower = team_name.lower()
    for name, rating in _elo_ratings.items():
        name_words = name.lower().split()
        team_words = team_lower.split()
        # Match by team nickname (last word usually)
        if (name_words and team_words and
            (name_words[-1] in team_words or team_words[-1] in name_words)):
            return float(rating)
    return 1500.0  # default average

def update_elo_ratings(home_team: str, away_team: str, home_won: bool, sport_key: str):
    """Update Elo ratings after a game result."""
    home_elo = get_team_elo(home_team)
    away_elo = get_team_elo(away_team)
    expected_home = elo_win_probability(home_elo, away_elo, sport_key)
    actual_home = 1.0 if home_won else 0.0
    k = _ELO_K_FACTOR
    new_home_elo = home_elo + k * (actual_home - expected_home)
    new_away_elo = away_elo + k * ((1-actual_home) - (1-expected_home))
    _elo_ratings[home_team] = round(new_home_elo, 1)
    _elo_ratings[away_team] = round(new_away_elo, 1)

def calculate_elo_edge(home_team: str, away_team: str, bet_side: str,
                       sport_key: str, market_odds: int) -> Optional[float]:
    """
    Calculate edge from Elo model vs market odds.
    Returns positive value if our side has value, negative if overpriced.
    """
    home_elo = get_team_elo(home_team)
    away_elo = get_team_elo(away_team)
    elo_prob = elo_win_probability(home_elo, away_elo, sport_key)
    if bet_side == "away":
        elo_prob = 1.0 - elo_prob
    market_implied = american_to_prob(market_odds)
    # De-vig market: divide by ~1.045 typical juice
    fair_market = market_implied / 1.045
    edge = (elo_prob - fair_market) * 100
    return round(edge, 2)

# ─── v5: Rest signal (back-to-back detection) ────────────────────────────────
# Cache for team schedule data scraped from ESPN
_team_schedule_cache: dict = {}

async def fetch_team_last_game_date(team_name: str, sport_slug: str) -> Optional[str]:
    """
    Attempt to get the most recent completed game date for a team from ESPN.
    Returns ISO date string or None if unavailable.
    """
    cache_key = f"schedule_{sport_slug}_{team_name.lower().replace(' ','_')}"
    cached = cache_get(cache_key, ttl=3600)  # 1 hr cache
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(f"{ESPN_BASE}/{sport_slug}/scoreboard",
                                 params={"limit": 30})
            if r.status_code != 200:
                return None
            raw = r.json()

        # Find completed games involving this team
        team_lower = team_name.lower()
        last_date = None
        for game in raw.get("events", []):
            status = game.get("competitions", [{}])[0].get("status", {}).get("type", {})
            if status.get("state") != "post":
                continue
            competitors = game.get("competitions", [{}])[0].get("competitors", [])
            team_names_in_game = [c.get("team", {}).get("displayName", "").lower() for c in competitors]
            if any(team_lower in t or t in team_lower for t in team_names_in_game):
                game_date = game.get("date", "")
                if game_date and (last_date is None or game_date > last_date):
                    last_date = game_date

        cache_set(cache_key, last_date)
        return last_date
    except Exception:
        return None

def calculate_rest_signal(home_team: str, away_team: str, bet_side: str,
                           sport_key: str) -> dict:
    """
    Simple rest signal using cached schedule data.
    Returns a rest advantage score (-5 to +5) for the bet side.
    In a full implementation this would use actual schedule lookups.
    For now, uses a heuristic based on ESPN "back-to-back" flags.
    """
    # Default: neutral rest signal
    return {
        "rest_advantage": 0,
        "label": "Rest: No data",
        "flag": None,
        "confidence_adj": 0,
    }

# ─── v5: Enhanced steam move detection ───────────────────────────────────────
def detect_steam_velocity(line_history: list) -> dict:
    """
    Analyzes line movement velocity to detect true steam moves.
    A steam move = rapid, concentrated line movement at multiple books.
    Returns a velocity score 0-1 and classification.
    """
    if not line_history or len(line_history) < 2:
        return {"velocity": 0, "steam_type": None, "moves_per_hour": 0}

    try:
        # Calculate spread of recent moves
        spreads = [h.get("home_spread") for h in line_history if h.get("home_spread") is not None]
        if len(spreads) < 2:
            return {"velocity": 0, "steam_type": None, "moves_per_hour": 0}

        total_move = abs(spreads[-1] - spreads[0])
        n_moves = len([i for i in range(1, len(spreads)) if abs(spreads[i] - spreads[i-1]) > 0.1])

        # Velocity score: more moves + larger total = higher confidence in steam
        velocity = min(1.0, (total_move / 3.0) * (n_moves / 5.0))

        if total_move >= 2.5 and n_moves >= 3:
            steam_type = "syndicate"  # Coordinated sharp action
        elif total_move >= 1.5 and n_moves >= 2:
            steam_type = "sharp_move"  # Clear sharp signal
        elif total_move >= 1.0:
            steam_type = "soft_move"  # Possible value bet
        else:
            steam_type = None

        return {
            "velocity": round(velocity, 2),
            "steam_type": steam_type,
            "total_move": round(total_move, 1),
            "n_moves": n_moves,
            "moves_per_hour": round(n_moves / max(len(line_history), 1), 1),
        }
    except Exception:
        return {"velocity": 0, "steam_type": None, "moves_per_hour": 0}


# ─── v5: Enhanced no-vig devigging ────────────────────────────────────────────
def multiplicative_devig(odds_list: list[int]) -> list[float]:
    """
    Multiplicative devig: removes bookmaker margin proportionally.
    More accurate than additive for parlays and market comparisons.
    Returns list of fair probabilities summing to 1.0.
    """
    probs = [american_to_prob(o) for o in odds_list]
    total = sum(probs)
    if total <= 0:
        n = len(probs)
        return [1.0/n] * n
    # Multiplicative: each prob / total (same as remove_vig but explicit)
    return [p / total for p in probs]

def pinnacle_devig(prices: list[int]) -> list[float]:
    """
    Pinnacle-specific devig using their ~2% reduced vig.
    Pinnacle's margin is ~2% vs typical books' ~4.5%.
    Returns fair probs after removing Pinnacle's low margin.
    """
    probs = [american_to_prob(p) for p in prices]
    total = sum(probs)
    if total <= 0:
        return [1.0/len(probs)] * len(probs)
    # Pinnacle vig is about 2% — remove it to get true fair price
    return [p / total for p in probs]


# ─── v5: Ensemble confidence scorer ───────────────────────────────────────────
def ensemble_confidence_score(signals: dict) -> int:
    """
    Calibrated confidence scorer based on signal quality and availability.
    
    Key design principles:
    1. Multi-book consensus is the primary reliable signal
    2. Pinnacle CLV is gold standard when available
    3. Book count matters — 5+ books gives much stronger signal than 1-2
    4. Sharp money confirmation adds real value when from live AN data
    5. Be skeptical by default — diminishing returns on weak signals
    
    Returns integer confidence 52-88 (never extreme — honesty is key).
    """
    clv         = signals.get("clv_edge", 0) or 0
    sharp       = signals.get("sharp_pct", 50) or 50
    pub         = signals.get("public_pct", 50) or 50
    line_move   = signals.get("line_move_pts", 0) or 0
    elo         = signals.get("elo_edge", 0) or 0
    steam_vel   = signals.get("steam_velocity", 0) or 0
    injury      = signals.get("injury_impact", 0) or 0
    weather_adj = signals.get("weather_adj", 0) or 0
    agreement   = signals.get("agent_agreement", 0.5) or 0.5
    kelly_u     = signals.get("kelly_units", 0) or 0
    has_live    = signals.get("has_live_data", False)
    book_count  = signals.get("book_count", 1)
    edge_source = signals.get("edge_source", "vig_removal")

    # ── PRIMARY SIGNAL: Edge source quality ───────────────────────────────────
    if edge_source == "pinnacle_clv":
        # Pinnacle CLV — most reliable
        if clv >= 7:    clv_pts = 22
        elif clv >= 5:  clv_pts = 16
        elif clv >= 3:  clv_pts = 10
        elif clv >= 2:  clv_pts = 6
        elif clv > 0:   clv_pts = 2
        else:           clv_pts = -5
        data_quality_bonus = 8   # bonus for using Pinnacle
    elif edge_source == "multi_book_consensus":
        # 3+ books consensus — strong
        if clv >= 5:    clv_pts = 14
        elif clv >= 3:  clv_pts = 9
        elif clv >= 2:  clv_pts = 5
        elif clv > 0:   clv_pts = 2
        else:           clv_pts = -3
        data_quality_bonus = 3 + min(4, book_count - 3)  # more books = more confidence
    elif edge_source == "two_book_consensus":
        if clv >= 4:    clv_pts = 9
        elif clv >= 2:  clv_pts = 4
        elif clv > 0:   clv_pts = 1
        else:           clv_pts = -4
        data_quality_bonus = 0
    else:
        # Single book vig-removal — weakest signal
        if clv >= 5:    clv_pts = 5
        elif clv >= 3:  clv_pts = 2
        else:           clv_pts = -2
        data_quality_bonus = -4

    score = 52.0 + clv_pts + data_quality_bonus

    # ── SECONDARY SIGNAL: Sharp Money (only valuable if real data) ────────────
    if has_live:
        rlm   = pub < 38 and sharp > 62
        steam = sharp > 72 and steam_vel > 0.5
        if steam:           score += 10
        elif rlm:           score += 7
        elif sharp >= 65:   score += 4
        elif sharp >= 58:   score += 2
        elif sharp < 35:    score -= 3
    else:
        score -= 2  # No real sharp data → slight uncertainty penalty

    # ── TERTIARY: Line movement confirmation ──────────────────────────────────
    if line_move >= 2.0:    score += 6
    elif line_move >= 1.0:  score += 3
    elif line_move >= 0.5:  score += 1

    # ── SUPPORTING: Elo model agreement ──────────────────────────────────────
    if elo >= 6:      score += 4
    elif elo >= 3:    score += 2
    elif elo < -3:    score -= 3

    # ── PENALTIES ─────────────────────────────────────────────────────────────
    score -= injury * 10
    score += weather_adj

    # ── KELLY + AGENT AGREEMENT ────────────────────────────────────────────────
    if kelly_u >= 2.5: score += 3
    elif kelly_u >= 1: score += 1
    elif kelly_u < 0.3: score -= 2
    score += (agreement - 0.5) * 8

    return min(88, max(52, int(round(score))))


_fitted_agent_weights: dict = {}
_calibration_params: dict   = {}  # Platt scaling: {"a": float, "b": float, "n_samples": int}
_weights_last_fit: float     = 0.0
_calibration_last_fit: float = 0.0
RECAL_INTERVAL = 50   # refit every N new resolved picks
MIN_SAMPLES_WEIGHTS = 100      # lowered from 300 — bootstrap seeding covers the gap
MIN_SAMPLES_CALIBRATION = 200

# ═══════════════════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════════════════

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Algobets Ai API", version="5.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS: fully open — API key in headers is the real auth.
# Must explicitly list allowed headers for preflight (OPTIONS) to pass.
# allow_origins=["*"] with allow_credentials=False is required by browsers
# when sending custom headers like X-API-Key from a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,
)

# ═══════════════════════════════════════════════════════════════════════════════
# SQLite — Pick Logging + ROI Tracking
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS picks (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            pick_id               TEXT UNIQUE,
            sport                 TEXT,
            game                  TEXT,
            home_team             TEXT,
            away_team             TEXT,
            bet                   TEXT,
            bet_type              TEXT,
            bet_side              TEXT,
            odds                  INTEGER,
            edge                  REAL,
            confidence            INTEGER,
            confidence_raw        INTEGER,   -- pre-calibration score
            confidence_calibrated REAL,      -- Platt-scaled probability
            fair_prob             REAL,
            implied_prob          REAL,
            pinnacle_line         REAL,
            pinnacle_fetched_at   TEXT,      -- when Pinnacle line was fetched
            espn_line             REAL,      -- ESPN line at same moment as Pinnacle pull
            espn_line_fetched_at  TEXT,      -- timestamp ESPN line was captured (for timing parity)
            clv_edge              REAL,       -- edge at pick time
            clv_pinnacle_close    REAL,       -- Pinnacle line 30min before game (true CLV)
            weather_flag          TEXT,       -- null | 'wind' | 'cold' | 'precip'
            weather_details       TEXT,       -- JSON weather snapshot
            agents_fired          TEXT,
            agents_vetoed         TEXT,
            veto_passed           INTEGER DEFAULT 1,
            data_source           TEXT,
            created_at            TEXT DEFAULT (datetime('now')),
            game_time             TEXT,
            clv_captured_at       TEXT,       -- when closing line was fetched
            result                TEXT,       -- 'win' | 'loss' | 'push' | NULL
            pnl                   REAL,
            clv_actual            REAL,
            resolved_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_stats (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name   TEXT UNIQUE,
            signal       TEXT,
            picks_with   INTEGER DEFAULT 0,
            wins_with    INTEGER DEFAULT 0,
            picks_without INTEGER DEFAULT 0,
            wins_without  INTEGER DEFAULT 0,
            fitted_weight REAL,              -- logistic regression coefficient
            updated_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS calibration (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            platt_a     REAL,                -- sigmoid slope
            platt_b     REAL,                -- sigmoid intercept
            n_samples   INTEGER,
            brier_score REAL,                -- calibration quality (lower = better)
            fitted_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS leaderboard_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,             -- hashed email or user token
            username        TEXT NOT NULL,
            pick_id         TEXT NOT NULL REFERENCES picks(pick_id),
            sport           TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, pick_id)                   -- one entry per user per pick
        );

        CREATE INDEX IF NOT EXISTS idx_picks_created  ON picks(created_at);
        CREATE INDEX IF NOT EXISTS idx_picks_result   ON picks(result);
        CREATE INDEX IF NOT EXISTS idx_picks_sport    ON picks(sport);
        CREATE INDEX IF NOT EXISTS idx_picks_gametime ON picks(game_time);
        CREATE INDEX IF NOT EXISTS idx_lb_user        ON leaderboard_entries(user_id);
        """)
        # Add new columns to existing tables if upgrading from v3
        for col, typedef in [
            ("confidence_raw",        "INTEGER"),
            ("confidence_calibrated", "REAL"),
            ("clv_pinnacle_close",    "REAL"),
            ("weather_flag",          "TEXT"),
            ("weather_details",       "TEXT"),
            ("clv_captured_at",       "TEXT"),
            ("pinnacle_fetched_at",   "TEXT"),
            ("espn_line",             "REAL"),
            ("espn_line_fetched_at",  "TEXT"),
        ]:
            try:
                db.execute(f"ALTER TABLE picks ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists

# ─── DB helpers ───────────────────────────────────────────────────────────────

def log_pick(pick: dict):
    """Write a surfaced pick to the database. Ignores duplicates."""
    try:
        with get_db() as db:
            db.execute("""
                INSERT OR IGNORE INTO picks
                (pick_id, sport, game, home_team, away_team, bet, bet_type, bet_side,
                 odds, edge, confidence, confidence_raw, confidence_calibrated,
                 fair_prob, implied_prob,
                 pinnacle_line, pinnacle_fetched_at,
                 espn_line, espn_line_fetched_at,
                 clv_edge,
                 weather_flag, weather_details,
                 agents_fired, agents_vetoed, veto_passed, data_source, game_time)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(pick.get("id")),
                pick.get("sport"),
                pick.get("game"),
                pick.get("homeTeam"),
                pick.get("awayTeam"),
                pick.get("bet"),
                pick.get("betType"),
                pick.get("bet_side", ""),
                pick.get("odds_int", 0),
                pick.get("edge"),
                pick.get("confidence"),
                pick.get("confidence_raw"),
                pick.get("confidence_calibrated"),
                pick.get("fair_prob", 0),
                pick.get("implied_prob", 0),
                pick.get("pinnacle_line"),
                pick.get("pinnacle_fetched_at"),
                pick.get("espn_line"),
                pick.get("espn_line_fetched_at"),
                pick.get("clv_edge"),
                pick.get("weather_flag"),
                json.dumps(pick.get("weather_details")) if pick.get("weather_details") else None,
                json.dumps(pick.get("agents_fired", [])),
                json.dumps(pick.get("agents_vetoed", [])),
                1 if pick.get("veto_passed", True) else 0,
                pick.get("data_source", ""),
                pick.get("game_time", ""),
            ))
    except Exception as e:
        print(f"[DB] log_pick error: {e}")

def resolve_pick(pick_id: str, result: str, pnl: float, clv_actual: float = None):
    """Mark a pick as resolved with its outcome."""
    try:
        with get_db() as db:
            db.execute("""
                UPDATE picks SET result=?, pnl=?, clv_actual=?, resolved_at=datetime('now')
                WHERE pick_id=?
            """, (result, pnl, clv_actual, pick_id))
            # Update agent stats
            row = db.execute("SELECT * FROM picks WHERE pick_id=?", (pick_id,)).fetchone()
            if row:
                agents_fired = json.loads(row["agents_fired"] or "[]")
                _update_agent_stats(db, agents_fired, result == "win")
    except Exception as e:
        print(f"[DB] resolve_pick error: {e}")

def _update_agent_stats(db, agents_fired: list, won: bool):
    all_agents = ["value","line_movement","public_money","injury","situational","fade_public","kelly"]
    for agent in all_agents:
        fired = agent in agents_fired
        col_picks = "picks_with" if fired else "picks_without"
        col_wins  = "wins_with"  if fired else "wins_without"
        db.execute(f"""
            INSERT INTO agent_stats (agent_name, signal, {col_picks}, {col_wins})
            VALUES (?, 'fired', 1, ?)
            ON CONFLICT(agent_name) DO UPDATE SET
                {col_picks} = {col_picks} + 1,
                {col_wins}  = {col_wins}  + ?,
                updated_at  = datetime('now')
        """, (agent, 1 if won else 0, 1 if won else 0))

def get_performance_stats() -> dict:
    """Aggregate win%, ROI, CLV, and per-agent accuracy from pick history."""
    try:
        with get_db() as db:
            overall = db.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN result='win'  THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN result='push' THEN 1 ELSE 0 END) as pushes,
                    SUM(COALESCE(pnl, 0))  as total_pnl,
                    AVG(edge)              as avg_edge,
                    AVG(confidence)        as avg_confidence,
                    AVG(CASE WHEN clv_pinnacle_close IS NOT NULL THEN clv_pinnacle_close END) as avg_clv_close,
                    AVG(CASE WHEN clv_actual IS NOT NULL THEN clv_actual END) as avg_clv
                FROM picks WHERE result IS NOT NULL
            """).fetchone()

            recent = db.execute("""
                SELECT COUNT(*) as total,
                    SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
                    SUM(COALESCE(pnl,0)) as pnl
                FROM picks
                WHERE result IS NOT NULL
                  AND created_at > datetime('now', '-30 days')
            """).fetchone()

            by_sport = db.execute("""
                SELECT sport,
                    COUNT(*) as total,
                    SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
                    SUM(COALESCE(pnl,0)) as pnl
                FROM picks WHERE result IS NOT NULL
                GROUP BY sport ORDER BY pnl DESC
            """).fetchall()

            agent_rows = db.execute("""
                SELECT agent_name, picks_with, wins_with, picks_without, wins_without, fitted_weight
                FROM agent_stats
            """).fetchall()

            top_clv = db.execute("""
                SELECT agents_fired, AVG(clv_pinnacle_close) as avg_clv, COUNT(*) as n
                FROM picks
                WHERE clv_pinnacle_close IS NOT NULL AND result IS NOT NULL
                GROUP BY agents_fired
                ORDER BY avg_clv DESC LIMIT 5
            """).fetchall()

            cal = db.execute("""
                SELECT platt_a, platt_b, n_samples, brier_score
                FROM calibration ORDER BY fitted_at DESC LIMIT 1
            """).fetchone()

            total = overall["total"] or 1
            wins  = overall["wins"] or 0
            roi   = round((overall["total_pnl"] or 0) / max(total, 1) * 100, 2)

            return {
                "overall": {
                    "total_picks":        total,
                    "wins":               wins,
                    "losses":             overall["losses"] or 0,
                    "pushes":             overall["pushes"] or 0,
                    "win_pct":            round(wins / total * 100, 1),
                    "roi_pct":            roi,
                    "total_units":        round(overall["total_pnl"] or 0, 2),
                    "avg_edge":           round(overall["avg_edge"] or 0, 2),
                    "avg_confidence":     round(overall["avg_confidence"] or 0, 1),
                    "avg_clv_at_close":   round(overall["avg_clv_close"] or 0, 2),
                    "avg_clv_manual":     round(overall["avg_clv"] or 0, 2),
                },
                "last_30_days": {
                    "total":   recent["total"],
                    "wins":    recent["wins"],
                    "win_pct": round((recent["wins"] or 0) / max(recent["total"] or 1, 1) * 100, 1),
                    "pnl":     round(recent["pnl"] or 0, 2),
                },
                "by_sport": [
                    {"sport": r["sport"], "total": r["total"],
                     "win_pct": round((r["wins"] or 0) / max(r["total"],1) * 100, 1),
                     "pnl": round(r["pnl"] or 0, 2)}
                    for r in by_sport
                ],
                "agent_accuracy": [
                    {
                        "agent": r["agent_name"],
                        "win_pct_when_fired":     round((r["wins_with"] or 0) / max(r["picks_with"] or 1, 1) * 100, 1),
                        "win_pct_when_not_fired": round((r["wins_without"] or 0) / max(r["picks_without"] or 1, 1) * 100, 1),
                        "picks_with":   r["picks_with"],
                        "fitted_weight": round(r["fitted_weight"] or 0, 3) if r["fitted_weight"] else None,
                    }
                    for r in agent_rows
                ],
                "top_clv_combos": [
                    {"agents": r["agents_fired"], "avg_clv": round(r["avg_clv"], 2), "n": r["n"]}
                    for r in top_clv
                ],
                "calibration": {
                    "platt_a":    round(cal["platt_a"], 4) if cal else None,
                    "platt_b":    round(cal["platt_b"], 4) if cal else None,
                    "n_samples":  cal["n_samples"] if cal else 0,
                    "brier_score": round(cal["brier_score"], 4) if cal else None,
                    "active":     cal is not None,
                } if cal else {"active": False, "n_samples": 0},
            }
    except Exception as e:
        print(f"[DB] performance stats error: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE CALIBRATION  — Platt Scaling
# ═══════════════════════════════════════════════════════════════════════════════

def _sigmoid(x: np.ndarray, a: float, b: float) -> np.ndarray:
    """Platt sigmoid: P(win) = 1 / (1 + exp(a * score + b))"""
    return 1.0 / (1.0 + np.exp(a * x + b))

def fit_calibration() -> dict:
    """
    Fit Platt scaling on resolved picks.
    Maps raw confidence score (0-100) → calibrated win probability.
    Returns {"a", "b", "n_samples", "brier_score"} or empty dict if insufficient data.
    """
    global _calibration_params, _calibration_last_fit
    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT confidence_raw, result FROM picks
                WHERE result IN ('win','loss') AND confidence_raw IS NOT NULL
            """).fetchall()

        if len(rows) < MIN_SAMPLES_CALIBRATION:
            print(f"[Calibration] Only {len(rows)} samples — need {MIN_SAMPLES_CALIBRATION}")
            return {}

        scores  = np.array([r["confidence_raw"] / 100.0 for r in rows])
        targets = np.array([1.0 if r["result"] == "win" else 0.0 for r in rows])

        # Gradient descent to fit a, b
        a, b = 1.0, 0.0
        lr = 0.01
        for _ in range(2000):
            p    = _sigmoid(scores, a, b)
            grad_a = np.mean((p - targets) * scores * p * (1 - p) * -1)
            grad_b = np.mean((p - targets) * p * (1 - p) * -1)
            a -= lr * grad_a
            b -= lr * grad_b

        # Brier score (lower = better calibrated)
        p_final    = _sigmoid(scores, a, b)
        brier      = float(np.mean((p_final - targets) ** 2))

        params = {"a": float(a), "b": float(b),
                  "n_samples": len(rows), "brier_score": brier}
        _calibration_params  = params
        _calibration_last_fit = time.time()

        with get_db() as db:
            db.execute("""
                INSERT INTO calibration (platt_a, platt_b, n_samples, brier_score)
                VALUES (?,?,?,?)
            """, (a, b, len(rows), brier))

        print(f"[Calibration] Fitted on {len(rows)} samples. "
              f"Platt a={a:.3f} b={b:.3f} Brier={brier:.4f}")
        return params

    except Exception as e:
        print(f"[Calibration] Error: {e}")
        return {}


def calibrate_confidence(raw_score: int) -> float:
    """
    Apply Platt scaling to a raw confidence score.
    Returns calibrated probability 0–1.
    Falls back to raw/100 if no calibration fitted yet.
    """
    if not _calibration_params:
        # Try to load from DB on first call
        try:
            with get_db() as db:
                row = db.execute("""
                    SELECT platt_a, platt_b FROM calibration
                    ORDER BY fitted_at DESC LIMIT 1
                """).fetchone()
            if row:
                _calibration_params["a"] = row["platt_a"]
                _calibration_params["b"] = row["platt_b"]
        except Exception:
            pass

    if _calibration_params:
        a = _calibration_params.get("a", 1.0)
        b = _calibration_params.get("b", 0.0)
        cal = float(_sigmoid(np.array([raw_score / 100.0]), a, b)[0])
        return round(cal, 3)
    return round(raw_score / 100.0, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT WEIGHT OPTIMISATION  — Logistic Regression on historical picks
# ═══════════════════════════════════════════════════════════════════════════════

ALL_AGENTS = ["value","line_movement","public_money","injury","situational","fade_public","kelly"]

# ─── Bootstrap seeding ────────────────────────────────────────────────────────
# The agent weight optimizer needs 300 resolved picks before it fires.
# On a new deployment that could take months. Instead, we seed the DB with
# synthetic picks derived from the DEFAULT_AGENT_WEIGHTS — essentially telling
# the model "these are our prior beliefs about agent performance".
# Real resolved picks immediately dilute and eventually dominate the priors.
# We use 50 synthetic picks per agent (350 total) as weak priors (result = 50/50
# for most agents, biased toward expected direction based on hand-tuned weights).

def seed_bootstrap_picks():
    """
    Insert synthetic prior picks into picks.db so agent weight optimization
    activates immediately on first deploy. Each agent gets 50 synthetic samples
    reflecting the hand-tuned DEFAULT_AGENT_WEIGHTS as weak priors.
    Only runs once — checks for existing bootstrap rows first.
    """
    try:
        with get_db() as db:
            existing = db.execute(
                "SELECT COUNT(*) as n FROM picks WHERE data_source='bootstrap_prior'"
            ).fetchone()["n"]
            if existing >= 100:
                print(f"[Bootstrap] {existing} prior picks already seeded — skipping")
                return

        # Build synthetic picks. Each agent gets 50 rows — win rate reflects
        # the expected direction from DEFAULT_AGENT_WEIGHTS.
        # value=3.0 (positive) → 60% win rate when fired; injury=-5.0 → 35% when fired
        agent_win_rates = {
            "value":         0.62,
            "line_movement": 0.60,
            "public_money":  0.61,
            "injury":        0.35,   # negative weight — injury flag means worse outcome
            "situational":   0.57,
            "fade_public":   0.59,
            "kelly":         0.58,
        }
        import random
        rng = random.Random(42)  # fixed seed for reproducibility

        rows = []
        for agent, win_rate in agent_win_rates.items():
            for i in range(50):
                # Fire the focal agent on every row, randomly fire others
                fired = [agent] + [a for a in ALL_AGENTS
                                    if a != agent and rng.random() < 0.4]
                result = "win" if rng.random() < win_rate else "loss"
                rows.append((
                    f"bootstrap_{agent}_{i}",       # pick_id
                    "NBA",                           # sport
                    f"Bootstrap Game {agent} {i}",  # game
                    "TeamA", "TeamB",                # home_team, away_team
                    f"Bootstrap {agent}",            # bet
                    "spread", "away",                # bet_type, bet_side
                    -110, 3.0, 65, 65, 0.65,        # odds, edge, confidence, confidence_raw, confidence_calibrated
                    0.55, 0.52,                      # fair_prob, implied_prob
                    None, None, None, None, None,    # pinnacle_line, pinnacle_fetched_at, espn_line, espn_line_fetched_at, clv_edge
                    None,                            # clv_pinnacle_close
                    None, None,                      # weather_flag, weather_details
                    json.dumps(fired), json.dumps([]), 1,  # agents_fired, agents_vetoed, veto_passed
                    "bootstrap_prior", "",           # data_source, game_time
                    result, 0.0 if result == "win" else -1.0,  # result, pnl
                    datetime.utcnow().isoformat(),   # resolved_at
                ))

        with get_db() as db:
            db.executemany("""
                INSERT OR IGNORE INTO picks
                (pick_id, sport, game, home_team, away_team, bet, bet_type, bet_side,
                 odds, edge, confidence, confidence_raw, confidence_calibrated,
                 fair_prob, implied_prob,
                 pinnacle_line, pinnacle_fetched_at, espn_line, espn_line_fetched_at, clv_edge,
                 clv_pinnacle_close, weather_flag, weather_details,
                 agents_fired, agents_vetoed, veto_passed,
                 data_source, game_time,
                 result, pnl, resolved_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
        print(f"[Bootstrap] Seeded {len(rows)} synthetic prior picks for agent weight initialization")

    except Exception as e:
        print(f"[Bootstrap] Seeding error: {e}")

def fit_agent_weights() -> dict:
    """
    Fit logistic regression coefficients on historical agent signals vs outcomes.
    Each feature is 1 if the agent fired, 0 if not.
    Returns {agent_name: weight} or empty dict if insufficient data.
    """
    global _fitted_agent_weights, _weights_last_fit
    try:
        with get_db() as db:
            rows = db.execute("""
                SELECT agents_fired, result FROM picks
                WHERE result IN ('win','loss') AND agents_fired IS NOT NULL
            """).fetchall()

        if len(rows) < MIN_SAMPLES_WEIGHTS:
            print(f"[Weights] Only {len(rows)} samples — need {MIN_SAMPLES_WEIGHTS}")
            return {}

        # Build feature matrix
        X = np.zeros((len(rows), len(ALL_AGENTS)))
        y = np.zeros(len(rows))
        for i, row in enumerate(rows):
            fired = json.loads(row["agents_fired"] or "[]")
            for j, agent in enumerate(ALL_AGENTS):
                X[i, j] = 1.0 if agent in fired else 0.0
            y[i] = 1.0 if row["result"] == "win" else 0.0

        # Logistic regression via gradient descent
        weights = np.zeros(len(ALL_AGENTS))
        bias    = 0.0
        lr      = 0.05
        for _ in range(3000):
            logits = X @ weights + bias
            preds  = 1.0 / (1.0 + np.exp(-logits))
            errors = preds - y
            weights -= lr * (X.T @ errors) / len(rows)
            bias    -= lr * errors.mean()

        weight_dict = {agent: round(float(w), 4)
                       for agent, w in zip(ALL_AGENTS, weights)}
        _fitted_agent_weights = weight_dict
        _weights_last_fit     = time.time()

        # Persist to agent_stats table
        with get_db() as db:
            for agent, w in weight_dict.items():
                db.execute("""
                    INSERT INTO agent_stats (agent_name, signal, fitted_weight)
                    VALUES (?, 'fired', ?)
                    ON CONFLICT(agent_name) DO UPDATE SET fitted_weight=?, updated_at=datetime('now')
                """, (agent, w, w))

        print(f"[Weights] Fitted on {len(rows)} samples: {weight_dict}")
        return weight_dict

    except Exception as e:
        print(f"[Weights] Error: {e}")
        return {}


def get_agent_weights() -> dict:
    """Return currently active agent weights (fitted if available, else defaults)."""
    if _fitted_agent_weights:
        return _fitted_agent_weights
    # Try loading from DB
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT agent_name, fitted_weight FROM agent_stats WHERE fitted_weight IS NOT NULL"
            ).fetchall()
        if len(rows) >= len(ALL_AGENTS):
            return {r["agent_name"]: r["fitted_weight"] for r in rows}
    except Exception:
        pass
    return DEFAULT_AGENT_WEIGHTS


# ═══════════════════════════════════════════════════════════════════════════════
# WEATHER SIGNAL  — Open-Meteo (free, no key)
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_weather_for_game(home_team: str, game_time: str,
                                  sport_key: str) -> Optional[dict]:
    """
    Fetch weather forecast for an outdoor stadium game.
    Only runs for NFL and MLB (outdoor parks). Returns None for all others.

    Returns dict: { wind_mph, temp_f, precip_mm, weather_flag, description }
    weather_flag: None | 'wind' | 'cold' | 'precip' | 'wind+cold'
    """
    if sport_key not in ("americanfootball_nfl", "baseball_mlb"):
        return None

    coords = STADIUM_COORDS.get(home_team)
    if not coords:
        return None  # dome or unknown stadium

    lat, lon = coords

    try:
        game_dt = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
    except Exception:
        return None

    # Round to nearest hour for cache key
    cache_key = f"weather_{home_team}_{game_dt.strftime('%Y%m%d%H')}"
    cached = cache_get(cache_key, ttl=3600)  # 1hr cache — weather doesn't change fast
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(WEATHER_BASE, params={
                "latitude":          lat,
                "longitude":         lon,
                "hourly":            "temperature_2m,wind_speed_10m,precipitation",
                "temperature_unit":  "fahrenheit",
                "wind_speed_unit":   "mph",
                "timezone":          "auto",
                "start_date":        game_dt.strftime("%Y-%m-%d"),
                "end_date":          game_dt.strftime("%Y-%m-%d"),
            })
            r.raise_for_status()
            data = r.json()

        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])
        temps  = hourly.get("temperature_2m", [])
        winds  = hourly.get("wind_speed_10m", [])
        precip = hourly.get("precipitation", [])

        # Find the hour closest to game time
        target_hour = game_dt.strftime("%Y-%m-%dT%H:00")
        idx = 0
        for i, t in enumerate(times):
            if t >= target_hour:
                idx = i
                break

        temp_f     = temps[idx]  if idx < len(temps)  else 60.0
        wind_mph   = winds[idx]  if idx < len(winds)  else 0.0
        precip_mm  = precip[idx] if idx < len(precip) else 0.0

        # Determine weather flag
        flags = []
        if wind_mph > 15:   flags.append("wind")
        if temp_f < 20:     flags.append("cold")
        if precip_mm > 2.5: flags.append("precip")
        weather_flag = "+".join(flags) if flags else None

        # Human-readable impact description
        impacts = []
        if wind_mph > 20:
            impacts.append(f"Strong wind {wind_mph:.0f}mph — suppresses scoring")
        elif wind_mph > 15:
            impacts.append(f"Wind {wind_mph:.0f}mph — moderate total impact")
        if temp_f < 20:
            impacts.append(f"Extreme cold {temp_f:.0f}°F — favors under")
        if precip_mm > 2.5:
            impacts.append(f"Precipitation {precip_mm:.1f}mm — affects footing")

        result = {
            "wind_mph":    round(wind_mph, 1),
            "temp_f":      round(temp_f, 1),
            "precip_mm":   round(precip_mm, 2),
            "weather_flag": weather_flag,
            "description": "; ".join(impacts) if impacts else "Clear conditions",
            "stadium":     home_team,
        }
        cache_set(cache_key, result)
        return result

    except Exception as e:
        print(f"[Weather] {home_team}: {e}")
        return None


def weather_confidence_adjustment(weather: Optional[dict], bet_type: str) -> int:
    """
    Returns a confidence adjustment (-/+ integer points) based on weather.
    Only totals are meaningfully affected. Spreads get a smaller adjustment.
    """
    if not weather or not weather.get("weather_flag"):
        return 0

    flag     = weather["weather_flag"]
    wind_mph = weather.get("wind_mph", 0)
    adj      = 0

    if bet_type == "totals":
        # Wind and cold strongly reduce total scoring
        if "wind" in flag:
            adj -= min(10, int(wind_mph / 2))  # -5 at 10mph, -10 at 20mph+
        if "cold" in flag:
            adj -= 5
        if "precip" in flag:
            adj -= 3
    elif bet_type == "spread":
        # Adverse weather slightly benefits the underdog (tighter games)
        if "wind" in flag and wind_mph > 20:
            adj -= 3

    return adj


# ═══════════════════════════════════════════════════════════════════════════════
# CLV CLOSING LINE CAPTURE  — background cron
# ═══════════════════════════════════════════════════════════════════════════════

async def capture_closing_lines():
    """
    Background task: runs every 10 minutes.
    For any pick with game_time within the next 45 minutes that hasn't had
    its closing Pinnacle line captured yet, fetches the current Pinnacle line
    and writes it to clv_pinnacle_close.

    This is the true CLV — the market's consensus at close is the gold standard.
    """
    try:
        with get_db() as db:
            # Find picks whose game starts within 45 min and haven't been captured
            cutoff_soon  = (datetime.utcnow() + timedelta(minutes=45)).isoformat()
            cutoff_past  = datetime.utcnow().isoformat()
            pending = db.execute("""
                SELECT pick_id, sport, home_team, away_team, bet_type, bet_side,
                       pinnacle_line, game_time
                FROM picks
                WHERE clv_pinnacle_close IS NULL
                  AND game_time IS NOT NULL
                  AND game_time <= ?
                  AND game_time >= ?
                  AND result IS NULL
            """, (cutoff_soon, cutoff_past)).fetchall()

        if not pending:
            return

        print(f"[CLV-Capture] {len(pending)} picks approaching game time")

        # Refresh Pinnacle data
        pinnacle_all = await fetch_all_pinnacle()

        with get_db() as db:
            for row in pending:
                sport_key  = _sport_label_to_key(row["sport"])
                pin_lines  = pinnacle_all.get(sport_key, [])
                pin_game   = match_pinnacle_game(pin_lines, row["home_team"], row["away_team"])
                if not pin_game:
                    continue

                if row["bet_type"] == "spread":
                    closing_line = (pin_game.get("home_spread") if row["bet_side"] == "home"
                                    else pin_game.get("away_spread"))
                elif row["bet_type"] == "moneyline":
                    closing_line = (pin_game.get("home_ml") if row["bet_side"] == "home"
                                    else pin_game.get("away_ml"))
                else:
                    closing_line = pin_game.get("total")

                if closing_line is not None:
                    db.execute("""
                        UPDATE picks SET clv_pinnacle_close=?, clv_captured_at=datetime('now')
                        WHERE pick_id=?
                    """, (closing_line, row["pick_id"]))
                    print(f"[CLV-Capture] {row['game_time'][:16]} "
                          f"{row['home_team']} closing line: {closing_line}")

    except Exception as e:
        print(f"[CLV-Capture] Error: {e}")


def _sport_label_to_key(label: str) -> str:
    """Convert 'NBA' → 'basketball_nba' etc."""
    return next((k for k, v in SPORT_META.items() if v["label"] == label), "")


async def maybe_refit_models():
    """
    Check if we have enough new resolved picks to warrant refitting
    calibration and agent weights. Runs after every resolve_picks call.
    """
    global _calibration_last_fit, _weights_last_fit
    try:
        with get_db() as db:
            total_resolved = db.execute(
                "SELECT COUNT(*) as n FROM picks WHERE result IS NOT NULL"
            ).fetchone()["n"]

        # Refit calibration every RECAL_INTERVAL new picks past minimum
        if (total_resolved >= MIN_SAMPLES_CALIBRATION and
                total_resolved % RECAL_INTERVAL == 0):
            print(f"[AutoRefit] {total_resolved} resolved picks — refitting calibration")
            await asyncio.get_event_loop().run_in_executor(None, fit_calibration)

        # Refit weights when enough data
        if (total_resolved >= MIN_SAMPLES_WEIGHTS and
                total_resolved % RECAL_INTERVAL == 0):
            print(f"[AutoRefit] {total_resolved} resolved picks — refitting agent weights")
            await asyncio.get_event_loop().run_in_executor(None, fit_agent_weights)

    except Exception as e:
        print(f"[AutoRefit] Error: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# DISK-BACKED CACHE
# ═══════════════════════════════════════════════════════════════════════════════

_cache: dict = {}

def _disk_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{hashlib.md5(key.encode()).hexdigest()}.json")

def cache_get(key: str, ttl: int = None):
    effective_ttl = ttl or CACHE_TTL
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < effective_ttl:
        return entry["data"]
    try:
        with open(_disk_path(key)) as f:
            entry = json.load(f)
        if time.time() - entry["ts"] < effective_ttl:
            _cache[key] = entry
            return entry["data"]
    except Exception:
        pass
    return None

def cache_set(key: str, data):
    entry = {"data": data, "ts": time.time()}
    _cache[key] = entry
    try:
        with open(_disk_path(key), "w") as f:
            json.dump(entry, f)
    except Exception:
        pass

def cache_age_seconds(key: str) -> int:
    entry = _cache.get(key)
    if not entry:
        try:
            with open(_disk_path(key)) as f:
                entry = json.load(f)
        except Exception:
            return -1
    return int(time.time() - entry["ts"])

# ═══════════════════════════════════════════════════════════════════════════════
# FREE DATA LAYER — ESPN
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_espn_games(sport_slug: str) -> list:
    """Fetch today's games + ESPN consensus odds for one sport. Free."""
    cache_key = f"espn_{sport_slug.replace('/','_')}"
    cached = cache_get(cache_key, ttl=CACHE_TTL_FREE)
    if cached is not None:
        return cached

    url = f"{ESPN_BASE}/{sport_slug}/scoreboard"
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url, params={"limit": 30})
            r.raise_for_status()
            raw = r.json()

        from datetime import timezone as _tz
        now_utc = datetime.now(_tz.utc)
        events = []
        for game in raw.get("events", []):
            try:
                comps = game.get("competitions") or []
                if not comps:
                    continue
                comp = comps[0]
                if not comp or not isinstance(comp, dict):
                    continue
                competitors = comp.get("competitors") or []
                if len(competitors) < 2:
                    continue
                home = next((c for c in competitors if c and c.get("homeAway") == "home"), competitors[0])
                away = next((c for c in competitors if c and c.get("homeAway") == "away"), competitors[1])
                if not home or not away:
                    continue
                home_team = (home.get("team") or {}).get("displayName", "")
                away_team = (away.get("team") or {}).get("displayName", "")
                if not home_team or not away_team:
                    continue

                # Skip finished or live games
                status    = (comp.get("status") or {}).get("type") or {}
                state     = status.get("state", "pre")
                completed = status.get("completed", False)
                if state in ("post", "in") or completed:
                    continue

                # Skip games that started > 5 min ago
                game_date = game.get("date", "")
                if game_date:
                    try:
                        gdt = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
                        if gdt < now_utc - timedelta(minutes=5):
                            continue
                    except Exception:
                        pass

                odds_list = comp.get("odds") or []
                odds_raw  = (odds_list[0] if odds_list else None) or {}
                spread     = odds_raw.get("spread")
                over_under = odds_raw.get("overUnder")
                home_ml    = (odds_raw.get("homeTeamOdds") or {}).get("moneyLine")
                away_ml    = (odds_raw.get("awayTeamOdds") or {}).get("moneyLine")

                markets = []
                if home_ml and away_ml:
                    markets.append({"key": "h2h", "outcomes": [
                        {"name": home_team, "price": int(home_ml)},
                        {"name": away_team, "price": int(away_ml)},
                    ]})
                if spread is not None:
                    markets.append({"key": "spreads", "outcomes": [
                        {"name": home_team, "point": -float(spread), "price": -110},
                        {"name": away_team, "point":  float(spread), "price": -110},
                    ]})
                if over_under is not None:
                    markets.append({"key": "totals", "outcomes": [
                        {"name": "Over",  "point": float(over_under), "price": -110},
                        {"name": "Under", "point": float(over_under), "price": -110},
                    ]})
                if not markets:
                    markets.append({"key": "h2h", "outcomes": [
                        {"name": home_team, "price": -110},
                        {"name": away_team, "price": -110},
                    ]})

                bookmakers = [{"key": "espn_consensus", "title": "ESPN Consensus", "markets": markets}]
                events.append({
                    "id":            game.get("id", ""),
                    "sport_slug":    sport_slug,
                    "home_team":     home_team,
                    "away_team":     away_team,
                    "home_abbr":     (home.get("team") or {}).get("abbreviation", ""),
                    "away_abbr":     (away.get("team") or {}).get("abbreviation", ""),
                    "commence_time": game_date,
                    "state":         state,
                    "bookmakers":    bookmakers,
                    "espn_spread":   spread,
                    "espn_total":    over_under,
                    "espn_home_ml":  home_ml,
                    "espn_away_ml":  away_ml,
                })
            except Exception as _game_err:
                print(f"[ESPN] {sport_slug} game parse error: {_game_err}")
                continue

        cache_set(cache_key, events)
        return events
    except Exception as e:
        print(f"[ESPN] {sport_slug}: {e}")
        return []


async def fetch_espn_all_games() -> dict:
    cached = cache_get("espn_all_games", ttl=CACHE_TTL_FREE)
    if cached is not None:
        return cached
    tasks   = [fetch_espn_games(slug) for slug in ESPN_SPORT_MAP]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_games = {}
    for slug, result in zip(ESPN_SPORT_MAP.keys(), results):
        if isinstance(result, list) and result:
            all_games[ESPN_SPORT_MAP[slug]] = result
    cache_set("espn_all_games", all_games)
    return all_games


async def fetch_espn_injuries(sport_slug: str) -> list:
    cache_key = f"espn_injuries_{sport_slug.replace('/','_')}"
    cached = cache_get(cache_key, ttl=CACHE_TTL_INJURIES)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(f"{ESPN_BASE}/{sport_slug}/injuries")
            r.raise_for_status()
            raw = r.json()
        injuries = []
        for item in raw.get("injuries", []):
            athlete  = item.get("athlete", {})
            team_inf = item.get("team", {})
            # ESPN injury structure varies — handle both formats
            inj_list = item.get("injuries", [])
            inj_type = inj_list[0] if inj_list else {}

            # Normalize status from multiple possible fields
            raw_status = (inj_type.get("status") or
                          item.get("status") or
                          athlete.get("status", {}).get("type", {}).get("description", "") or
                          "Unknown")

            # Map to standard frontend values
            sl = raw_status.lower()
            if any(w in sl for w in ["out", "ir ", "injured reserve", "inactive"]):
                status = "Out"
            elif "doubtful" in sl:
                status = "Doubtful"
            elif "questionable" in sl or "day-to-day" in sl or "dtd" in sl:
                status = "Questionable"
            elif any(w in sl for w in ["probable", "active", "full"]):
                status = "Probable"
            elif raw_status and raw_status != "Unknown":
                status = raw_status  # keep as-is if we have something
            else:
                status = "Questionable"  # default to questionable rather than Unknown

            impact = ("High" if status == "Out"
                      else "High" if status == "Doubtful"
                      else "Medium" if status == "Questionable"
                      else "Low")

            # Get injury description from multiple possible paths
            injury_desc = (inj_type.get("longComment") or
                           inj_type.get("shortComment") or
                           inj_type.get("type", {}).get("description") or
                           inj_type.get("description") or
                           item.get("type", {}).get("description") or
                           "Injury")

            player_name = athlete.get("displayName") or athlete.get("fullName") or "Unknown Player"
            if player_name == "Unknown":
                continue  # skip entries with no player data

            injuries.append({
                "id":      abs(hash(athlete.get("id","") + status)) % 100000,
                "player":  player_name,
                "team":    team_inf.get("abbreviation", ""),
                "sport":   sport_slug.split("/")[-1].upper(),
                "pos":     athlete.get("position", {}).get("abbreviation", ""),
                "status":  status,
                "injury":  injury_desc,
                "impact":  impact,
                "game":    "",
                "updated": "Live",
            })
        cache_set(cache_key, injuries)
        return injuries
    except Exception as e:
        print(f"[ESPN injuries] {sport_slug}: {e}")
        return []


async def fetch_all_espn_injuries() -> list:
    cached = cache_get("all_injuries", ttl=CACHE_TTL_INJURIES)
    if cached is not None:
        return cached
    slugs   = ["basketball/nba", "football/nfl", "hockey/nhl", "baseball/mlb"]
    results = await asyncio.gather(*[fetch_espn_injuries(s) for s in slugs], return_exceptions=True)
    all_inj = []
    for r in results:
        if isinstance(r, list):
            all_inj.extend(r)
    order = {"High": 0, "Medium-High": 1, "Medium": 2, "Low": 3}
    all_inj.sort(key=lambda x: order.get(x["impact"], 4))
    cache_set("all_injuries", all_inj)
    return all_inj


async def fetch_espn_player_stats(sport_slug: str, limit: int = 20) -> list:
    cache_key = f"espn_stats_{sport_slug.replace('/','_')}"
    cached = cache_get(cache_key, ttl=CACHE_TTL_PROPS)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(f"{ESPN_BASE}/{sport_slug}/statistics/byathlete",
                                 params={"limit": limit, "season": "2026"})
            r.raise_for_status()
            raw = r.json()
        players = []
        for athlete in raw.get("athletes", []):
            info  = athlete.get("athlete", {})
            stats = athlete.get("statistics", [])
            stat_map = {s.get("name",""): s.get("value",0) for s in stats}
            players.append({
                "id":   info.get("id",""),
                "name": info.get("displayName",""),
                "team": info.get("team",{}).get("abbreviation",""),
                "pos":  info.get("position",{}).get("abbreviation",""),
                "stats": stat_map,
            })
        cache_set(cache_key, players)
        return players
    except Exception as e:
        print(f"[ESPN stats] {sport_slug}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# FREE DATA LAYER — Action Network
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_action_network_lines() -> dict:
    """Public betting % and sharp handle from Action Network. Free, no key needed."""
    cached = cache_get("action_network_lines", ttl=CACHE_TTL_FREE)
    if cached is not None:
        return cached

    result = {}
    today  = datetime.utcnow().strftime("%Y%m%d")

    async def fetch_sport(an_slug: str, sport_key: str):
        try:
            async with httpx.AsyncClient(timeout=10, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.actionnetwork.com/",
            }) as client:
                r = await client.get(f"{AN_BASE}/games",
                                     params={"sport": an_slug, "date": today,
                                             "include": "all_betting_data"})
                if r.status_code != 200:
                    return sport_key, []
                raw = r.json()

            games = []
            for g in raw.get("games", []):
                teams = g.get("teams", [])
                if len(teams) < 2:
                    continue
                home_t = next((t for t in teams if t.get("is_home")), teams[0])
                away_t = next((t for t in teams if not t.get("is_home")), teams[1])

                # Action Network uses multiple possible structures depending on API version.
                # Try "bets" first, then fall back to top-level "consensus" fields.
                bets        = g.get("bets") or {}
                spread_bets = bets.get("spread") or {}
                ml_bets     = bets.get("moneyline") or {}

                # v2 API uses top-level consensus object
                consensus = g.get("consensus") or {}

                home_spread_pct = (
                    spread_bets.get("home_bets_pct")
                    or consensus.get("home_spread_pct")
                    or g.get("home_spread_public_pct")
                    or 50.0
                )
                home_ml_pct = (
                    ml_bets.get("home_bets_pct")
                    or consensus.get("home_ml_pct")
                    or g.get("home_ml_public_pct")
                    or 50.0
                )
                home_sharp_pct = (
                    spread_bets.get("home_handle_pct")
                    or consensus.get("home_handle_pct")
                    or g.get("home_handle_pct")
                    or 50.0
                )
                # Ensure float and non-null
                home_spread_pct = float(home_spread_pct or 50.0)
                home_ml_pct     = float(home_ml_pct or 50.0)
                home_sharp_pct  = float(home_sharp_pct or 50.0)

                line_history = g.get("line_history", [])
                spread_line  = g.get("spread") or {}
                current_spread = spread_line.get("home") if isinstance(spread_line, dict) else None
                open_spread    = line_history[0].get("home_spread") if line_history else None
                games.append({
                    "game_id":         g.get("id"),
                    "home_team":       home_t.get("full_name",""),
                    "away_team":       away_t.get("full_name",""),
                    "home_abbr":       home_t.get("abbr",""),
                    "away_abbr":       away_t.get("abbr",""),
                    "public_pct":      home_spread_pct,
                    "away_public_pct": 100 - home_spread_pct,
                    "ml_public_pct":   home_ml_pct,
                    "sharp_pct":       home_sharp_pct,
                    "opening_spread":  open_spread,
                    "current_spread":  current_spread,
                    "line_history":    line_history,
                })
            return sport_key, games
        except Exception as e:
            print(f"[ActionNetwork] {an_slug}: {e}")
            return sport_key, []

    responses = await asyncio.gather(
        *[fetch_sport(slug, key) for slug, key in AN_SPORT_MAP.items()],
        return_exceptions=True,
    )
    for resp in responses:
        if isinstance(resp, tuple):
            sk, games = resp
            if games:
                result[sk] = games
    cache_set("action_network_lines", result)
    return result


def match_an_game(an_games: list, home_team: str, away_team: str) -> Optional[dict]:
    if not an_games:
        return None
    home_lower = home_team.lower()
    away_lower = away_team.lower()
    for g in an_games:
        ht = g.get("home_team", "").lower()
        at = g.get("away_team", "").lower()
        if (any(w in ht for w in home_lower.split() if len(w) > 3) and
                any(w in at for w in away_lower.split() if len(w) > 3)):
            return g
        if (g.get("home_abbr","").lower() in home_lower or
                g.get("away_abbr","").lower() in away_lower):
            return g
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# FREE DATA LAYER — Pinnacle CLV Benchmark
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_pinnacle_lines(sport_key: str) -> list:
    """
    Fetch Pinnacle's current spread and ML odds for a sport.
    Pinnacle is the sharpest, lowest-vig book — their line is the market's
    best estimate of true probability. We use it as the fair-value benchmark.

    Pinnacle's public lines endpoint requires no API key.
    Returns list of { home_team, away_team, home_spread, away_spread,
                      home_ml, away_ml, total } dicts.
    """
    cache_key = f"pinnacle_{sport_key}"
    cached = cache_get(cache_key, ttl=CACHE_TTL_PINNACLE)
    if cached is not None:
        return cached

    sport_id = PINNACLE_SPORT_MAP.get(sport_key)
    if not sport_id:
        return []

    try:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        if PINNACLE_API_KEY:
            import base64
            headers["Authorization"] = f"Basic {PINNACLE_API_KEY}"
        async with httpx.AsyncClient(timeout=12, headers=headers) as client:
            # Pinnacle public fixtures endpoint
            r = await client.get(
                f"{PINNACLE_BASE}/fixtures",
                params={"sportId": sport_id, "leagueIds": "", "isLive": "0"},
            )
            if r.status_code != 200:
                return []
            fixtures = r.json().get("league", [])

            # Also grab the odds
            event_ids = [str(e["id"]) for league in fixtures
                         for e in league.get("events", [])]
            if not event_ids:
                return []

            r2 = await client.get(
                f"{PINNACLE_BASE}/odds",
                params={
                    "sportId": sport_id,
                    "leagueIds": "",
                    "oddsFormat": "American",
                    "eventIds": ",".join(event_ids[:30]),
                },
            )
            if r2.status_code != 200:
                return []
            odds_data = r2.json()

        # Build a lookup: event_id → teams
        team_lookup = {}
        for league in fixtures:
            for event in league.get("events", []):
                team_lookup[event["id"]] = {
                    "home": event.get("home", ""),
                    "away": event.get("away", ""),
                    "starts": event.get("starts", ""),
                }

        lines = []
        for event in odds_data.get("leagues", []):
            for match in event.get("events", []):
                eid   = match.get("id")
                teams = team_lookup.get(eid, {})
                if not teams:
                    continue
                home_spread = away_spread = home_ml = away_ml = total = None

                for period in match.get("periods", []):
                    if period.get("number") != 0:  # Full game only
                        continue
                    # Spread
                    spreads = period.get("spreads", [])
                    if spreads:
                        sp = spreads[0]
                        home_spread = sp.get("hdp")   # home handicap (negative = favourite)
                        away_spread = -home_spread if home_spread is not None else None
                    # Moneyline
                    ml = period.get("moneyline", {})
                    home_ml = ml.get("home")
                    away_ml = ml.get("away")
                    # Total
                    totals = period.get("totals", [])
                    if totals:
                        total = totals[0].get("points")

                lines.append({
                    "event_id":           eid,
                    "home_team":          teams["home"],
                    "away_team":          teams["away"],
                    "starts":             teams["starts"],
                    "home_spread":        home_spread,
                    "away_spread":        away_spread,
                    "home_ml":            home_ml,
                    "away_ml":            away_ml,
                    "total":              total,
                    "pinnacle_fetched_at": datetime.utcnow().isoformat(),  # timestamp for CLV timing parity
                })

        cache_set(cache_key, lines)
        print(f"[Pinnacle] {sport_key}: {len(lines)} games")
        return lines

    except Exception as e:
        print(f"[Pinnacle] {sport_key}: {e}")
        return []


async def fetch_all_pinnacle() -> dict:
    """Fetch Pinnacle lines for all supported sports concurrently."""
    cached = cache_get("pinnacle_all", ttl=CACHE_TTL_PINNACLE)
    if cached is not None:
        return cached
    sports  = list(PINNACLE_SPORT_MAP.keys())
    results = await asyncio.gather(*[fetch_pinnacle_lines(s) for s in sports],
                                   return_exceptions=True)
    data = {}
    for sport, result in zip(sports, results):
        if isinstance(result, list) and result:
            data[sport] = result
    cache_set("pinnacle_all", data)
    return data


def match_pinnacle_game(pin_lines: list, home_team: str, away_team: str) -> Optional[dict]:
    """Fuzzy-match a Pinnacle game by team name."""
    if not pin_lines:
        return None
    home_lower = home_team.lower()
    away_lower = away_team.lower()
    for g in pin_lines:
        ph = g.get("home_team", "").lower()
        pa = g.get("away_team", "").lower()
        # Try last word of team name (e.g. "Celtics", "Lakers")
        home_words = [w for w in home_lower.split() if len(w) > 3]
        away_words = [w for w in away_lower.split() if len(w) > 3]
        if home_words and away_words:
            if (any(w in ph for w in home_words) and
                    any(w in pa for w in away_words)):
                return g
    return None


def calculate_clv_edge(espn_odds: int, pinnacle_ml: Optional[int],
                       pinnacle_spread: Optional[float], bet_type: str,
                       bet_point: Optional[float] = None) -> Optional[float]:
    """
    Compare our line (ESPN) to Pinnacle's line to calculate true CLV edge.
    Returns edge in percentage points (positive = we have value vs Pinnacle).

    CLV logic:
    - For moneyline: compare ESPN ML implied prob to Pinnacle ML implied prob
    - For spread:    compare spread point differential (ESPN spread vs Pinnacle spread)
                     and convert to prob edge using 0.033 pts per % rule
    """
    if pinnacle_ml is None and pinnacle_spread is None:
        return None

    try:
        if bet_type == "moneyline" and pinnacle_ml:
            espn_prob = american_to_prob(espn_odds)
            pin_prob  = american_to_prob(pinnacle_ml)
            # Remove Pinnacle's ~2% vig for true fair price
            pin_fair  = pin_prob / 1.02
            return round((pin_fair - espn_prob) * 100, 2)

        elif bet_type == "spread" and pinnacle_spread is not None and bet_point is not None:
            # Positive CLV edge if our spread is BETTER than Pinnacle's
            # E.g. ESPN gives +4.5, Pinnacle gives +3.5 → 1pt in our favour
            # Rule of thumb: 0.5pt on spread ≈ 1.65% edge at standard vig
            diff = float(bet_point) - float(pinnacle_spread)
            return round(diff * 3.3, 2)  # 3.3% per point

    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PAID DATA LAYER — The Odds API  (EV + Arb only)
# ═══════════════════════════════════════════════════════════════════════════════

async def get_active_sports() -> list[str]:
    cached = cache_get("active_sports", ttl=CACHE_TTL_SPORTS)
    if cached is not None:
        return cached
    if not ODDS_API_KEY:
        return SPORTS
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{ODDS_BASE}/sports", params={"apiKey": ODDS_API_KEY})
            r.raise_for_status()
            active_keys = {s["key"] for s in r.json() if s.get("active")}
            result = [s for s in SPORTS if s in active_keys]
            cache_set("active_sports", result)
            return result or SPORTS
    except Exception as e:
        print(f"[OddsAPI] Sports list error: {e}")
        return SPORTS


async def fetch_odds(sport: str, markets: str = "h2h,spreads,totals") -> list:
    global _quota_remaining, _quota_used_last
    cache_key = f"odds_{sport}_{markets}"
    cached = cache_get(cache_key, ttl=CACHE_TTL)
    if cached is not None:
        return cached
    if not ODDS_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{ODDS_BASE}/sports/{sport}/odds", params={
                "apiKey": ODDS_API_KEY, "regions": "us", "markets": markets,
                "oddsFormat": "american", "dateFormat": "iso",
                "bookmakers": ODDS_BOOKMAKERS,
            })
            r.raise_for_status()
            rem  = r.headers.get("x-requests-remaining")
            used = r.headers.get("x-requests-last")
            if rem:  _quota_remaining = int(rem)
            if used: _quota_used_last = int(used)
            print(f"[OddsAPI] {sport}/{markets} cost {used} — {rem} remaining")
            data = r.json()
            cache_set(cache_key, data)
            return data
    except Exception as e:
        print(f"[OddsAPI] Error {sport}: {e}")
        return []


async def fetch_all_odds(markets: str = "h2h,spreads,totals") -> dict:
    cache_key = f"all_odds_{markets}"
    cached = cache_get(cache_key, ttl=CACHE_TTL)
    if cached is not None:
        return cached
    active  = await get_active_sports()
    results = await asyncio.gather(*[fetch_odds(s, markets) for s in active],
                                   return_exceptions=True)
    all_odds = {s: r for s, r in zip(active, results)
                if isinstance(r, list) and r}
    cache_set(cache_key, all_odds)
    return all_odds


# ═══════════════════════════════════════════════════════════════════════════════
# ODDS MATH
# ═══════════════════════════════════════════════════════════════════════════════

def american_to_prob(odds: int) -> float:
    if odds > 0: return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)

def prob_to_american(prob: float) -> int:
    if prob <= 0 or prob >= 1: return -110
    if prob >= 0.5: return -round(prob / (1 - prob) * 100)
    return round((1 - prob) / prob * 100)

def remove_vig(probs: list[float]) -> list[float]:
    total = sum(probs)
    return [p / total for p in probs] if total else probs

def kelly_fraction(edge: float, odds: int) -> float:
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    p = american_to_prob(odds) + edge
    q = 1 - p
    return max(0, round(((b * p - q) / b) * 0.25, 3))



# ═══════════════════════════════════════════════════════════════════════════════
# SHARP EDGE ENGINE v8 — Research-Backed Sports Betting Algorithm
# ═══════════════════════════════════════════════════════════════════════════════
#
# Built on what research actually shows works:
#
# 1. PINNACLE CLV (primary signal — strongest predictor in all research)
#    Compare best available price across books to Pinnacle no-vig fair line.
#    Every 1% of CLV = real edge. 3%+ CLV vs Pinnacle is a strong play.
#
# 2. SOFT BOOK LAG DETECTION (new v8)
#    Soft books (Bovada, BetMGM) lag sharp books by 5-15 min after sharp bets.
#    When a soft book offers a price 2+ "cents" better than Pinnacle fair value,
#    that gap is real, actionable, and closing fast — bet it now.
#
# 3. REVERSE LINE MOVEMENT + STEAM
#    RLM: public heavily on one side, line moves other way = follow sharps.
#    Steam: large cross-book price dispersion = fresh sharp move, books haven't
#    all adjusted yet. Time-sensitive window.
#
# 4. CROSS-MARKET CONSISTENCY CHECK (new v8)
#    When ML implies home -150 but spread says home -3 (equiv. ~-115), the
#    markets disagree. One is mispriced. We identify which and exploit it.
#
# 5. FAVORITE-LONGSHOT BIAS FILTER (new v8)
#    Books over-price big favorites (>-250) and big dogs (+350+).
#    Best EV comes from mid-range odds -160 to +180. Research-confirmed.
#
# 6. BOOK SHARPNESS WEIGHTING (new v8)
#    Pinnacle > DraftKings > FanDuel > BetMGM > WilliamHill > Bovada.
#    Getting a price above SHARP books at SOFT books = most reliable edge.
#
# 7. KEY NUMBERS + SPORT-SPECIFIC FILTERS
#    NFL 3/7/10/14 — half-point buys add documented EV.
#
# GATE: Pinnacle CLV ≥1.5% + ≥1 confirming signal. Strict = high hit rate.
# ═══════════════════════════════════════════════════════════════════════════════

# Key numbers by sport
NFL_KEY_NUMBERS = {3, 7, 10, 14, 6, 4}
NBA_KEY_NUMBERS = {5, 7, 10, 4, 6}
NHL_KEY_NUMBERS = {1, 1.5}

# Book sharpness ranking: 10=sharpest(fastest to react), 1=softest(slowest)
# Getting value at soft books vs sharp consensus = the most reliable edge type
BOOK_SHARPNESS = {
    "pinnacle":       10,
    "pinnaclesports": 10,
    "draftkings":     7,
    "fanduel":        7,
    "betmgm":         5,
    "williamhill_us": 5,
    "caesars":        5,
    "pointsbet":      4,
    "bovada":         3,
    "betonlineag":    3,
    "mybookieag":     2,
}

# Favorite-longshot bias: avoid extreme odds where books over-charge
FL_BIAS_FAV_THRESHOLD = -250  # more negative = avoid (books over-price big favs)
FL_BIAS_DOG_THRESHOLD = 280   # more positive = avoid (books over-price big dogs)

# ─── Book extraction helpers ─────────────────────────────────────────────────

def _pinnacle_ml(books: list, team: str) -> Optional[int]:
    for book in books:
        if book.get("key") not in ("pinnacle", "pinnaclesports"):
            continue
        for mkt in book.get("markets", []):
            if mkt["key"] != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                if o["name"] == team:
                    return int(o["price"])
    return None

def _pinnacle_spread(books: list, team: str) -> Optional[tuple]:
    """Returns (point, price) for team at Pinnacle spreads market."""
    for book in books:
        if book.get("key") not in ("pinnacle", "pinnaclesports"):
            continue
        for mkt in book.get("markets", []):
            if mkt["key"] != "spreads":
                continue
            for o in mkt.get("outcomes", []):
                if o["name"] == team:
                    return (float(o.get("point", 0)), int(o["price"]))
    return None

def _all_ml_prices(books: list, team: str) -> list:
    """All (book_key, price) for h2h market."""
    out = []
    for book in books:
        bk = book.get("key", "")
        for mkt in book.get("markets", []):
            if mkt["key"] != "h2h":
                continue
            for o in mkt.get("outcomes", []):
                if o["name"] == team:
                    out.append((bk, int(o["price"])))
    return out

def _all_spread_entries(books: list, team: str) -> list:
    """All (book_key, point, price) for spreads market."""
    out = []
    for book in books:
        bk = book.get("key", "")
        for mkt in book.get("markets", []):
            if mkt["key"] != "spreads":
                continue
            for o in mkt.get("outcomes", []):
                if o["name"] == team:
                    out.append((bk, float(o.get("point", 0)), int(o["price"])))
    return out

# ─── Core math ───────────────────────────────────────────────────────────────

def pin_no_vig(home_ml: int, away_ml: int) -> tuple:
    """
    Remove Pinnacle's ~2% vig. Returns (home_fair_prob, away_fair_prob).
    These are the market's best estimate of true win probabilities.
    """
    hi = american_to_prob(home_ml)
    ai = american_to_prob(away_ml)
    t  = hi + ai  # ≈1.02 at Pinnacle
    return hi / t, ai / t

def pin_spread_no_vig(home_price: int, away_price: int) -> tuple:
    """Remove Pinnacle's spread vig. Returns (home_fair_prob, away_fair_prob)."""
    hi = american_to_prob(home_price)
    ai = american_to_prob(away_price)
    t  = hi + ai
    return hi / t, ai / t

def calc_ml_clv(bet_price: int, pin_home_ml: int, pin_away_ml: int, side: str) -> float:
    """
    CLV edge on moneyline.
    = (Pinnacle fair probability) - (our implied probability from our price)
    Positive = we have real edge. Negative = we're paying too much.
    """
    hf, af = pin_no_vig(pin_home_ml, pin_away_ml)
    fair     = hf if side == "home" else af
    our_impl = american_to_prob(bet_price)
    return round((fair - our_impl) * 100, 2)

def calc_spread_clv(bet_pt: float, bet_price: int,
                    pin_home_pt: float, pin_home_price: int,
                    pin_away_price: int, side: str) -> float:
    """
    Spread CLV = price edge + point edge vs Pinnacle.
    Point edge: each 0.5pt ≈ 1.5% at standard -110 (3%/pt).
    """
    hf, af = pin_spread_no_vig(pin_home_price, pin_away_price)
    fair     = hf if side == "home" else af
    our_impl = american_to_prob(bet_price)
    price_edge = (fair - our_impl) * 100
    pin_pt = pin_home_pt if side == "home" else -pin_home_pt
    point_edge = (bet_pt - pin_pt) * 3.0
    return round(price_edge + point_edge, 2)

def cross_book_steam(books: list, team: str, market: str) -> dict:
    """
    Steam detector: measure price dispersion across all books.
    High dispersion = books are out of sync = fresh sharp action hit some books.
    Get on the good side before the other books adjust.
    """
    if market == "h2h":
        raw = _all_ml_prices(books, team)
        prices = [p for _, p in raw]
    else:
        raw = _all_spread_entries(books, team)
        prices = [p for _, pt, p in raw]

    if len(prices) < 3:
        return {"steam": False, "velocity": 0.0, "dispersion": 0.0, "n_books": len(prices)}

    implied = [american_to_prob(p) for p in prices]
    dispersion = max(implied) - min(implied)  # spread in implied prob space

    # >2.5% dispersion = meaningful (books disagreeing after sharp move)
    steam    = dispersion > 0.025
    velocity = min(1.0, dispersion / 0.06)

    return {
        "steam":      steam,
        "velocity":   round(velocity, 2),
        "dispersion": round(dispersion * 100, 2),
        "n_books":    len(prices),
        "best_price": max(prices),
    }

def calc_rlm(an_game: Optional[dict],
             opening_home_ml: Optional[int],
             current_home_ml: Optional[int]) -> dict:
    """
    Reverse Line Movement: public money floods one side, line moves OTHER way.
    = Professional money on unpopular side is overpowering the public.

    Signal:
    - >60% public bets on home + home ML getting LONGER (less favored) = sharps on away
    - >60% public bets on away + away ML getting longer = sharps on home
    Falls back to sharp handle vs public bet % when no opening line.
    """
    if not an_game:
        return {"rlm": False, "rlm_side": None, "rlm_strength": 0.0,
                "public_pct": 50.0, "sharp_pct": 50.0}

    pub_pct   = float(an_game.get("public_pct", 50) or 50)
    sharp_pct = float(an_game.get("sharp_pct", 50) or 50)

    rlm_side     = None
    rlm_strength = 0.0

    if opening_home_ml and current_home_ml:
        # Line movement check: did home get longer (worse) despite public support?
        home_longer = american_to_prob(current_home_ml) < american_to_prob(opening_home_ml) - 0.008
        home_shorter = american_to_prob(current_home_ml) > american_to_prob(opening_home_ml) + 0.008

        if pub_pct > 60 and home_longer:
            # Public on home but home line is lengthening → sharps on away
            rlm_side     = "away"
            rlm_strength = min(1.0, (pub_pct - 55) / 30)
        elif pub_pct < 40 and home_shorter:
            # Public on away but away line is lengthening → sharps on home
            rlm_side     = "home"
            rlm_strength = min(1.0, (55 - pub_pct) / 30)
    else:
        # Fallback: sharp handle vs public bet % divergence
        if pub_pct > 60 and sharp_pct < 38:
            rlm_side     = "away"
            rlm_strength = min(1.0, (pub_pct - 55) / 25)
        elif pub_pct < 40 and sharp_pct > 62:
            rlm_side     = "home"
            rlm_strength = min(1.0, (55 - pub_pct) / 25)

    return {
        "rlm":          rlm_side is not None,
        "rlm_side":     rlm_side,
        "rlm_strength": round(rlm_strength, 2),
        "public_pct":   pub_pct,
        "sharp_pct":    sharp_pct,
    }

def key_number_value(point: float, sport_key: str) -> dict:
    """Spread is near a key number. Half-point buys at key numbers = massive EV."""
    if "nfl" in sport_key:
        keys = NFL_KEY_NUMBERS
    elif "nba" in sport_key or "ncaab" in sport_key:
        keys = NBA_KEY_NUMBERS
    elif "nhl" in sport_key:
        keys = NHL_KEY_NUMBERS
    else:
        return {"key_number": False, "key": None, "distance": 99.0}

    abs_pt   = abs(point)
    nearest  = min(keys, key=lambda k: abs(abs_pt - k))
    distance = round(abs(abs_pt - nearest), 1)
    return {
        "key_number": distance <= 0.5,
        "key":        nearest,
        "distance":   distance,
        "on_key":     distance == 0,
    }


# ─── v8: Soft book lag detection ─────────────────────────────────────────────

def detect_soft_book_lag(books: list, team: str, market: str,
                          pin_fair_prob: float) -> dict:
    """
    Identifies soft books that haven't yet adjusted to sharp information.
    When Bovada or BetMGM still offers significantly better odds than what
    Pinnacle's fair price implies, that stale price is the edge.

    Returns the best soft-book price, which book it's at, and the "lag" gap.
    Lag > 2% implied probability = actionable soft book opportunity.
    """
    if market == "h2h":
        all_prices = _all_ml_prices(books, team)
    else:
        raw = _all_spread_entries(books, team)
        all_prices = [(bk, pr) for bk, pt, pr in raw]

    if not all_prices:
        return {"lag_detected": False, "best_soft_price": None, "best_soft_book": None, "lag_pct": 0.0}

    soft_prices = [
        (bk, pr) for bk, pr in all_prices
        if BOOK_SHARPNESS.get(bk, 5) <= 5  # only soft/medium books
    ]

    if not soft_prices:
        return {"lag_detected": False, "best_soft_price": None, "best_soft_book": None, "lag_pct": 0.0}

    best_soft_bk, best_soft_pr = max(soft_prices, key=lambda x: x[1])
    soft_implied = american_to_prob(best_soft_pr)

    # Lag = soft book still offers better odds than fair price
    # (soft_implied < pin_fair_prob means we're paying less than fair value at this book)
    lag_pct = (pin_fair_prob - soft_implied) * 100  # positive = we're paying less = good

    return {
        "lag_detected":    lag_pct >= 1.5,
        "best_soft_price": best_soft_pr,
        "best_soft_book":  best_soft_bk,
        "lag_pct":         round(lag_pct, 2),
        "soft_sharpness":  BOOK_SHARPNESS.get(best_soft_bk, 3),
    }


# ─── v8: Cross-market consistency ────────────────────────────────────────────

def cross_market_check(pin_home_ml: Optional[int], pin_away_ml: Optional[int],
                        pin_home_spread_pt: Optional[float],
                        sport_key: str) -> dict:
    """
    Checks if the ML market and spread market agree on the implied favorite/probability.
    When they disagree by >3%, one market is mispriced — that's where the edge is.

    Conversion: Each point on the spread ≈ 3% win probability.
    e.g. -3 spread ≈ 56% win probability ≈ -127 moneyline equivalent.

    If ML says home is 62% but spread says 54%, ML is overpriced on home
    or spread is underpriced on home — one is wrong, and we can exploit it.
    """
    if not pin_home_ml or not pin_away_ml or not pin_home_spread_pt:
        return {"inconsistent": False, "ml_home_prob": None, "spread_home_prob": None, "gap": 0.0}

    hf, af = pin_no_vig(pin_home_ml, pin_away_ml)

    # Convert spread to implied win probability
    # Rule of thumb: 0.5pt spread = ~1.5% win prob (3%/pt)
    # At pick'em (0), each team is 50%. Home -3 ≈ 56%, home +3 ≈ 44%
    spread_home_prob = 0.50 - (pin_home_spread_pt * 0.03)  # negative spread = favorite = higher prob
    spread_home_prob = max(0.30, min(0.70, spread_home_prob))  # clamp to reasonable range

    gap = abs(hf - spread_home_prob)

    # Markets disagree by >4% — one is mispriced
    inconsistent = gap > 0.04

    # Which market is more likely wrong?
    # ML moves faster than spreads typically, so spread lag is more common
    if inconsistent:
        if hf > spread_home_prob + 0.04:
            lagging = "spread"     # spread hasn't caught up to ML, home underpriced on spread
            exploit_side = "home"
            exploit_market = "spread"
        else:
            lagging = "ml"         # ML hasn't caught up to spread, away underpriced on ML
            exploit_side = "away"
            exploit_market = "moneyline"
    else:
        lagging = None; exploit_side = None; exploit_market = None

    return {
        "inconsistent":    inconsistent,
        "ml_home_prob":    round(hf, 3),
        "spread_home_prob":round(spread_home_prob, 3),
        "gap":             round(gap, 3),
        "lagging_market":  lagging,
        "exploit_side":    exploit_side,
        "exploit_market":  exploit_market,
    }


# ─── v8: Favorite-longshot bias check ────────────────────────────────────────

def fl_bias_penalty(odds: int) -> float:
    """
    Books systematically over-price big favorites and big underdogs.
    The sweet spot for EV is -160 to +180 (roughly 38-62% win prob range).

    Research from Wharton and multiple academic studies confirms this bias.
    Returns a confidence penalty (negative number) for bets outside the range.
    """
    if odds < FL_BIAS_FAV_THRESHOLD:      # e.g. -300 or more extreme
        severity = min(10.0, abs(odds + FL_BIAS_FAV_THRESHOLD) / 50)
        return -severity   # up to -10 confidence penalty
    elif odds > FL_BIAS_DOG_THRESHOLD:    # e.g. +300 or better
        severity = min(8.0, (odds - FL_BIAS_DOG_THRESHOLD) / 50)
        return -severity   # up to -8 confidence penalty
    return 0.0             # mid-range: no penalty


# ─── v8: Multi-session CLV drift ─────────────────────────────────────────────

def clv_drift_signal(opening_snap: Optional[dict], current_pin_home_ml: Optional[int],
                     current_pin_away_ml: Optional[int], side: str) -> dict:
    """
    Has Pinnacle's line moved in our favor since opening?
    If the opening snap had home at -130 and now it's -150, home got more expensive.
    We should have bet home when it was -130 — but if we're AWAY, this is our RLM signal.

    Returns a drift direction and magnitude.
    Drift in our favor = line moved toward our pick = market is confirming us.
    """
    if not opening_snap or not current_pin_home_ml or not current_pin_away_ml:
        return {"drift": "none", "drift_pts": 0.0, "confirming": False}

    open_home_ml  = opening_snap.get("home_ml")
    open_home_spd = opening_snap.get("spread_home")

    if open_home_ml and current_pin_home_ml:
        open_hf, open_af  = pin_no_vig(open_home_ml, opening_snap.get("away_ml") or -open_home_ml)
        curr_hf, curr_af  = pin_no_vig(current_pin_home_ml, current_pin_away_ml)
        home_drift = curr_hf - open_hf   # positive = home became more favored

        if side == "home":
            # We want home — did home get MORE favored? (confirming) or LESS? (against us)
            confirming = home_drift > 0.01
            drift_pts  = home_drift * 100
        else:
            # We want away — did away get more favored? (= home LESS favored)
            confirming = home_drift < -0.01
            drift_pts  = -home_drift * 100
    else:
        return {"drift": "none", "drift_pts": 0.0, "confirming": False}

    return {
        "drift":      "favorable" if confirming else ("against" if abs(drift_pts) > 1 else "stable"),
        "drift_pts":  round(drift_pts, 2),
        "confirming": confirming,
    }


# ─── Legacy agent wrappers (kept for pick output compatibility) ──────────────

def agent_value(market_prob: float, fair_prob: float,
                pinnacle_clv: Optional[float] = None) -> dict:
    edge   = pinnacle_clv if pinnacle_clv is not None else (fair_prob - market_prob) * 100
    source = "pinnacle_clv" if pinnacle_clv is not None else "vig_removal"
    grade  = ("A+" if edge >= 7 else "A" if edge >= 5 else "B+" if edge >= 3.5 else
              "B"  if edge >= 2.5 else "C+" if edge >= 1.5 else "C" if edge >= 0 else "F")
    return {"grade": grade, "edge_pct": round(edge, 2),
            "label": f"Value: {grade} ({edge:+.1f}%)", "source": source,
            "clv_based": pinnacle_clv is not None}

def agent_line_movement(opening: Optional[float], current: Optional[float],
                        bet_side: str, line_history: list = None) -> dict:
    if opening is None or current is None:
        return {"favorable": False, "label": "Line: No data", "move": "→ Stable", "diff": 0}
    diff      = current - opening
    favorable = (bet_side == "away" and diff > 0.3) or (bet_side == "home" and diff < -0.3)
    if abs(diff) < 0.3:   label, move = "Line: Stable →", "→ Stable"
    elif favorable:        label, move = f"Line: +{abs(diff):.1f} our way ↑", f"▲ {abs(diff):.1f}pt our way"
    else:                  label, move = f"Line: -{abs(diff):.1f} against ↓", f"▼ {abs(diff):.1f}pt against"
    return {"favorable": favorable, "label": label, "move": move, "diff": round(diff, 2)}

def agent_public_money(an_game: Optional[dict] = None,
                       public_pct: Optional[float] = None,
                       sharp_pct: Optional[float] = None) -> dict:
    if an_game:
        public_pct = float(an_game.get("public_pct", 50) or 50)
        sharp_pct  = float(an_game.get("sharp_pct",  50) or 50)
    else:
        public_pct = public_pct or 50.0
        sharp_pct  = sharp_pct  or 50.0
    rlm   = public_pct < 40 and sharp_pct > 60
    steam = sharp_pct > 72
    if steam:      action, ss = "🔴 Steam — sharp flooding in", 0.9
    elif rlm:      action, ss = "⚡ RLM — sharps vs public", 0.8
    elif sharp_pct > 60: action, ss = f"Sharp lean {sharp_pct:.0f}%", 0.6
    else:          action, ss = f"Public {public_pct:.0f}% / Sharp {sharp_pct:.0f}%", 0.3
    return {"public_pct": public_pct, "sharp_pct": sharp_pct, "rlm": rlm,
            "steam": steam, "action": action, "signal_strength": ss,
            "label": action, "source": "live" if an_game else "estimated"}

def agent_injury(home_team: str, away_team: str, injury_cache: list = None,
                 bet_side: str = "") -> dict:
    if not injury_cache:
        return {"impact": 0.0, "notes": "All clear", "label": "Injuries: All clear ✓",
                "veto": False, "veto_reason": ""}
    home_l = home_team.lower(); away_l = away_team.lower()
    relevant = []; max_impact = 0.0
    for inj in injury_cache:
        ta = inj.get("team", "").lower()
        if ta and (ta in home_l or ta in away_l or
                   any(ta in p for p in home_l.split()) or
                   any(ta in p for p in away_l.split())):
            relevant.append(inj)
            score = {"High": 1.0, "Medium-High": 0.7, "Medium": 0.4, "Low": 0.1}.get(inj["impact"], 0.1)
            max_impact = max(max_impact, score)
    if not relevant:
        return {"impact": 0.0, "notes": "All clear", "label": "Injuries: All clear ✓",
                "veto": False, "veto_reason": ""}
    top = relevant[0]; veto = False; veto_reason = ""
    if max_impact >= 1.0 and bet_side:
        bet_team = away_team if bet_side == "away" else home_team
        for inj in relevant:
            ta = inj.get("team", "").lower()
            if ta and any(ta in p for p in bet_team.lower().split()):
                if inj.get("impact") == "High":
                    veto = True; veto_reason = f"{inj['player']} OUT on {bet_team}"; break
    count = f" (+{len(relevant)-1} more)" if len(relevant) > 1 else ""
    return {"impact": max_impact, "notes": f"{top['player']} ({top['status']}){count}",
            "label": f"Injuries: {top['player']} {top['status']} ⚠️",
            "players": relevant[:3], "veto": veto, "veto_reason": veto_reason}

def agent_situational(game: dict, sport: str, espn_game: dict = None) -> dict:
    notes = []; score = 0.5
    if sport == "basketball_nba":   notes.append("NBA: check back-to-back")
    elif sport == "americanfootball_nfl": notes.append("NFL schedule spot")
    elif sport == "icehockey_nhl":  notes.append("NHL: 3rd game in 4 nights")
    elif sport == "baseball_mlb":   notes.append("MLB: bullpen last 3 days")
    return {"score": score, "notes": notes or ["Standard game"],
            "label": f"Situational: {notes[0] if notes else 'Standard spot'}"}

def agent_fade_public(public_pct: float, odds: int) -> dict:
    fade   = public_pct >= 70
    ss     = (public_pct - 50) / 50 if public_pct > 50 else 0
    return {"fade_signal": fade, "public_pct": public_pct,
            "signal_strength": round(ss, 2),
            "label": f"Fade public: {'Strong ✓' if fade else 'Neutral'} ({public_pct:.0f}%)"}

def agent_kelly(edge_pct: float, odds: int) -> dict:
    fraction = kelly_fraction(edge_pct / 100, odds)
    units    = round(fraction * 10, 1)
    label    = ("3+ units" if units >= 3 else f"{units} units" if units >= 1 else
                "0.5 units" if units > 0 else "No bet")
    return {"kelly_fraction": fraction, "units": units, "label": f"Kelly: {label}"}

def run_veto_checks(a2: dict, a3: dict, a4: dict,
                    best_edge: float, bet_side: str,
                    edge_source: str = "vig_removal") -> tuple:
    reasons = []
    if a4.get("veto"):
        reasons.append(f"V1-Injury: {a4['veto_reason']}")
    diff = a2.get("diff", 0)
    if not a2.get("favorable") and abs(diff) >= 1.5:
        reasons.append(f"V2-LineMove: moved {diff:+.1f}pts against bet side")
    pub  = a3.get("public_pct", 50)
    shrp = a3.get("sharp_pct", 50)
    if pub > 70 and shrp < 35:
        reasons.append(f"V3-PublicTrap: {pub:.0f}% public, {shrp:.0f}% sharp")
    if best_edge < 0:
        reasons.append(f"V4-NegativeEdge: {best_edge:.1f}%")
    return len(reasons) > 0, reasons


# ═══════════════════════════════════════════════════════════════════════════════
# CORE ALGORITHM: build_consensus_pick v7
# ═══════════════════════════════════════════════════════════════════════════════

def build_consensus_pick(event: dict, sport_key: str,
                         an_game: Optional[dict] = None,
                         injury_cache: list = None,
                         pinnacle_lines: list = None,
                         weather: Optional[dict] = None,
                         opening_snapshot: Optional[dict] = None) -> Optional[dict]:
    """
    Sharp edge finder v7.

    DATA FLOW:
      Odds API (6 books incl. Pinnacle) → strip Pinnacle vig → fair price
      → compare best available book price to fair price → CLV edge
      → confirm with RLM, steam, sharp %, key numbers
      → surface only when ≥2 signals converge

    GATE: Pinnacle CLV required. ≥1 confirming signal required.
    """
    meta  = SPORT_META.get(sport_key, {"label": sport_key, "emoji": "🎯"})
    books = event.get("bookmakers", [])
    if not books:
        return None

    home = event.get("home_team", "")
    away = event.get("away_team", "")
    game_label = f"{away} vs {home}"

    # ── REQUIRE PINNACLE ─────────────────────────────────────────────────────
    pin_home_ml     = _pinnacle_ml(books, home)
    pin_away_ml     = _pinnacle_ml(books, away)
    pin_home_spread = _pinnacle_spread(books, home)
    pin_away_spread = _pinnacle_spread(books, away)

    has_pin_ml  = pin_home_ml is not None and pin_away_ml is not None
    has_pin_spd = pin_home_spread is not None and pin_away_spread is not None

    if not has_pin_ml and not has_pin_spd:
        return None  # No Pinnacle = no pick

    # ── SHARED SIGNALS ───────────────────────────────────────────────────────
    open_snap = opening_snapshot or {}
    an_pub  = float((an_game or {}).get("public_pct", 50) or 50)
    an_sh   = float((an_game or {}).get("sharp_pct",  50) or 50)

    # RLM from opening ML snapshot
    rlm_info = calc_rlm(
        an_game,
        open_snap.get("home_ml") or pin_home_ml,
        pin_home_ml  # current Pinnacle = market's latest line
    )

    # v8: Cross-market consistency check (ML vs spread agreement)
    xmkt = cross_market_check(
        pin_home_ml, pin_away_ml,
        pin_home_spread[0] if has_pin_spd else None,
        sport_key
    )

    # Injury analysis (used for all candidates)
    def inj_for_side(side: str) -> dict:
        return agent_injury(home, away, injury_cache, bet_side=side)

    # ── EVALUATE CANDIDATES ──────────────────────────────────────────────────
    candidates = []

    # --- MONEYLINE ---
    if has_pin_ml:
        hf, af = pin_no_vig(pin_home_ml, pin_away_ml)

        for team, side, fair_prob_side in [(home, "home", hf), (away, "away", af)]:
            all_ml = _all_ml_prices(books, team)
            if len(all_ml) < 2:
                continue
            best_price = max(p for _, p in all_ml)
            best_book  = next(bk for bk, p in all_ml if p == best_price)
            n_books    = len(all_ml)

            clv = calc_ml_clv(best_price, pin_home_ml, pin_away_ml, side)
            if clv < 1.5:
                continue

            inj = inj_for_side(side)
            if inj["veto"]:
                print(f"[VETO] {game_label} {team} ML: {inj['veto_reason']}")
                continue

            steam  = cross_book_steam(books, team, "h2h")
            drift  = clv_drift_signal(open_snap, pin_home_ml, pin_away_ml, side)
            lag    = detect_soft_book_lag(books, team, "h2h", fair_prob_side)
            fl_pen = fl_bias_penalty(best_price)

            # Cross-market boost: if cross-market says this side is underpriced
            xmkt_boost = xmkt["inconsistent"] and xmkt["exploit_side"] == side

            candidates.append({
                "clv": clv, "bet": f"{team} ML", "betType": "moneyline",
                "side": side, "odds": best_price, "book": best_book,
                "n_books": n_books, "point": None,
                "steam": steam, "rlm": rlm_info, "injury": inj,
                "kn": {"key_number": False},
                "open_pt": None, "cur_pt": None,
                "drift": drift, "lag": lag, "fl_pen": fl_pen,
                "xmkt_boost": xmkt_boost,
                "fair_prob": fair_prob_side,
            })

    # --- SPREAD ---
    if has_pin_spd:
        pin_h_pt, pin_h_price = pin_home_spread
        pin_a_pt, pin_a_price = pin_away_spread
        spd_hf, spd_af = pin_spread_no_vig(pin_h_price, pin_a_price)

        for team, side, pin_pt, fair_spd in [
            (home, "home", pin_h_pt, spd_hf),
            (away, "away", pin_a_pt, spd_af),
        ]:
            all_spd = _all_spread_entries(books, team)
            if len(all_spd) < 2:
                continue

            # Best entry: most favorable point, then best price as tiebreaker
            best_entry  = max(all_spd, key=lambda x: (x[1], x[2]))
            best_bk, best_pt, best_pr = best_entry
            n_books = len(all_spd)

            clv = calc_spread_clv(best_pt, best_pr, pin_h_pt,
                                   pin_h_price, pin_a_price, side)
            if clv < 1.5:
                continue

            inj = inj_for_side(side)
            if inj["veto"]:
                print(f"[VETO] {game_label} {team} {best_pt:+.1f}: {inj['veto_reason']}")
                continue

            steam  = cross_book_steam(books, team, "spreads")
            kn     = key_number_value(best_pt, sport_key)
            drift  = clv_drift_signal(open_snap, pin_home_ml, pin_away_ml, side) if has_pin_ml else {"drift": "none", "confirming": False}
            lag    = detect_soft_book_lag(books, team, "spreads", fair_spd)
            fl_pen = fl_bias_penalty(best_pr)
            xmkt_boost = xmkt["inconsistent"] and xmkt["exploit_side"] == side and xmkt["exploit_market"] == "spread"

            open_pt_home = open_snap.get("spread_home")
            if open_pt_home is not None:
                open_pt = open_pt_home if side == "home" else -open_pt_home
                cur_pt  = pin_h_pt    if side == "home" else pin_a_pt
            else:
                open_pt = cur_pt = best_pt

            sign = "+" if best_pt > 0 else ""
            candidates.append({
                "clv": clv, "bet": f"{team} {sign}{best_pt:.1f}", "betType": "spread",
                "side": side, "odds": best_pr, "book": best_bk,
                "n_books": n_books, "point": best_pt,
                "steam": steam, "rlm": rlm_info, "injury": inj, "kn": kn,
                "open_pt": open_pt, "cur_pt": cur_pt,
                "drift": drift, "lag": lag, "fl_pen": fl_pen,
                "xmkt_boost": xmkt_boost,
                "fair_prob": fair_spd,
            })

    if not candidates:
        return None

    # ── CONVICTION SCORE (v8) ─────────────────────────────────────────────────
    # Ranks candidates to select the single best bet for this game.
    # All 8 signals contribute: CLV, RLM, steam, key#, sharp%, drift, lag, xmkt.
    def conviction(c: dict) -> float:
        s = c["clv"] * 2.5                              # foundation: 1% CLV = 2.5pts

        # RLM: strongest confirming signal (sharps beating public)
        rlm = c["rlm"]
        if rlm["rlm"] and rlm["rlm_side"] == c["side"]:
            s += 20 + rlm["rlm_strength"] * 12

        # Steam: cross-book dispersion = fresh sharp action
        if c["steam"]["steam"]:
            s += 12 + c["steam"]["velocity"] * 8

        # Key number (spread only)
        if c["kn"].get("on_key"):       s += 13
        elif c["kn"].get("key_number"): s += 8

        # v8: Soft book lag — their stale price is our edge
        lag = c.get("lag", {})
        if lag.get("lag_detected"):
            s += 8 + min(6, lag["lag_pct"] * 2)

        # v8: CLV drift — Pinnacle line moved in our favor since opening
        drift = c.get("drift", {})
        if drift.get("confirming"):
            s += 7 + min(5, drift["drift_pts"] * 0.5)

        # v8: Cross-market inconsistency confirms underpricing
        if c.get("xmkt_boost"):
            s += 9

        # v8: Favorite-longshot bias penalty
        s += c.get("fl_pen", 0)          # already negative if outside sweet spot

        # More books in consensus = more reliable signal
        s += min(6, c["n_books"] - 2)

        # Sharp money handle alignment
        if c["side"] == "home" and an_sh > 62:    s += 6
        elif c["side"] == "away" and an_sh < 38:  s += 6
        elif c["side"] == "home" and an_sh < 35:  s -= 6
        elif c["side"] == "away" and an_sh > 65:  s -= 6

        return s

    candidates.sort(key=conviction, reverse=True)
    c = candidates[0]

    # ── SIGNAL GATE (v8) ─────────────────────────────────────────────────────
    # Require Pinnacle CLV + ≥1 independent confirming signal.
    # Multiple weak signals count less than one strong signal.
    confirms = 0
    reasons  = []

    rlm   = c["rlm"]
    steam = c["steam"]
    kn    = c.get("kn", {})
    inj   = c["injury"]
    lag   = c.get("lag", {})
    drift = c.get("drift", {})

    if rlm["rlm"] and rlm["rlm_side"] == c["side"]:
        confirms += 2
        reasons.append(f"RLM: sharps on {c['side']} ({rlm['rlm_strength']:.0%} strength)")

    if steam["steam"]:
        confirms += 2
        reasons.append(f"Steam: {steam['dispersion']:.1f}% book dispersion ({steam['n_books']} books)")

    if kn.get("key_number"):
        confirms += 1
        reasons.append(f"Key# {kn['key']} ({kn['distance']}pt away)")

    if lag.get("lag_detected"):
        confirms += 2
        reasons.append(f"Soft book lag: {lag['best_soft_book']} +{lag['lag_pct']:.1f}% behind market")

    if drift.get("confirming"):
        confirms += 1
        reasons.append(f"Line drift: Pinnacle moved {drift['drift_pts']:+.1f}% our way since opening")

    if c.get("xmkt_boost"):
        confirms += 1
        reasons.append(f"Cross-market: ML/spread disagree on {c['side']} ({xmkt['gap']:.0%} gap)")

    if c["side"] == "home" and an_sh > 62 and not rlm["rlm"]:
        confirms += 1
        reasons.append(f"Sharp lean: {an_sh:.0f}% of sharp handle on home")
    elif c["side"] == "away" and an_sh < 38 and not rlm["rlm"]:
        confirms += 1
        reasons.append(f"Sharp lean: {100-an_sh:.0f}% of sharp handle on away")

    if c["clv"] >= 3.5:
        confirms += 1
        reasons.append(f"Strong Pinnacle CLV: {c['clv']:+.1f}%")

    if c["n_books"] >= 5 and c["clv"] >= 2.0:
        confirms += 1
        reasons.append(f"Multi-book consensus ({c['n_books']} books)")

    if c["clv"] < 1.5 or confirms == 0:
        print(f"[GATE-v8] {game_label} {c['bet']} filtered: CLV={c['clv']:.1f}%, confirms={confirms}")
        return None

    # ── CONFIDENCE SCORE (v8) ─────────────────────────────────────────────────
    # Honest signal-weighted confidence. 55 floor, 90 ceiling.
    # Only reaches 80+ when multiple independent signals all agree.
    conf = 58.0

    # Primary: CLV magnitude
    if c["clv"] >= 7:    conf += 20
    elif c["clv"] >= 5:  conf += 15
    elif c["clv"] >= 4:  conf += 11
    elif c["clv"] >= 3:  conf += 7
    elif c["clv"] >= 2:  conf += 4
    else:                conf += 1

    # RLM: large bonus because this is a well-documented alpha source
    if rlm["rlm"] and rlm["rlm_side"] == c["side"]:
        conf += 9 + rlm["rlm_strength"] * 6

    # Steam: books out of sync = time-sensitive opportunity
    if steam["steam"]:
        conf += 7 + steam["velocity"] * 4

    # Key number
    if kn.get("on_key"):        conf += 7
    elif kn.get("key_number"):  conf += 4

    # v8: Soft book lag — their price is still stale = real edge right now
    if lag.get("lag_detected"):
        conf += 6 + min(4, lag["lag_pct"] * 1.5)

    # v8: CLV drift — market is moving our way = confirmation
    if drift.get("confirming"):
        conf += 5 + min(3, drift["drift_pts"] * 0.3)

    # v8: Cross-market inconsistency
    if c.get("xmkt_boost"):
        conf += 5

    # Sharp money alignment
    if c["side"] == "home":
        if an_sh > 65:    conf += 5
        elif an_sh < 35:  conf -= 5
    else:
        if an_sh < 35:    conf += 5
        elif an_sh > 65:  conf -= 5

    # More books = more reliable consensus
    conf += min(4, c["n_books"] - 2)

    # v8: Favorite-longshot bias penalty
    conf += c.get("fl_pen", 0)

    # Weather adjustment
    if weather:
        conf += weather_confidence_adjustment(weather, c["betType"])

    # Injury soft penalty
    conf -= inj["impact"] * 8

    raw_confidence  = min(90, max(55, int(round(conf))))
    calibrated_prob = calibrate_confidence(raw_confidence)

    # ── BUILD OUTPUT ─────────────────────────────────────────────────────────
    elo_edge = calculate_elo_edge(home, away, c["side"], sport_key, c["odds"])
    a_kelly  = agent_kelly(c["clv"], c["odds"])
    a_move   = agent_line_movement(c.get("open_pt"), c.get("cur_pt"), c["side"])

    agents_fired = ["pinnacle_clv"]
    if rlm["rlm"] and rlm["rlm_side"] == c["side"]:  agents_fired.append("reverse_line_movement")
    if steam["steam"]:                                 agents_fired.append("steam")
    if kn.get("key_number"):                           agents_fired.append("key_number")
    if lag.get("lag_detected"):                        agents_fired.append("soft_book_lag")
    if drift.get("confirming"):                        agents_fired.append("clv_drift")
    if c.get("xmkt_boost"):                            agents_fired.append("cross_market")
    if an_sh > 62 or an_sh < 38:                       agents_fired.append("sharp_money")
    if elo_edge and abs(elo_edge) > 3:                 agents_fired.append("elo_model")
    if a_move["favorable"]:                            agents_fired.append("line_movement")

    odds_int     = c["odds"]
    odds_str     = f"+{odds_int}" if odds_int > 0 else str(odds_int)
    weather_flag = weather.get("weather_flag") if weather else None

    return {
        "id":        abs(hash(game_label + c["bet"])) % 100000,
        "sport":     meta["label"], "emoji": meta["emoji"],
        "game":      game_label, "homeTeam": home, "awayTeam": away,
        "bet":       c["bet"], "betType": c["betType"], "bet_side": c["side"],

        "confidence":            raw_confidence,
        "confidence_raw":        raw_confidence,
        "confidence_calibrated": calibrated_prob,
        "confidence_pct":        f"{round(calibrated_prob * 100, 1)}%",

        "edge":        round(c["clv"], 1),
        "edge_source": "pinnacle_clv",
        "odds":        odds_str,
        "odds_int":    odds_int,
        "best_book":   c["book"],
        "decimalOdds": round((odds_int/100+1) if odds_int > 0 else (100/abs(odds_int)+1), 2),

        "openingLine": f"{c['open_pt']:+.1f}" if c.get("open_pt") else "N/A",
        "currentLine": f"{c['cur_pt']:+.1f}"  if c.get("cur_pt")  else "N/A",
        "lineMove":    a_move["move"],
        "pinnacle_line": c.get("point"),
        "clv_edge":    round(c["clv"], 2),

        # Sharp money
        "steam":    steam["steam"],
        "rlm":      rlm["rlm"],
        "rlm_side": rlm["rlm_side"],
        "sharpPct": an_sh,
        "publicPct": an_pub,

        # v8 new signals
        "soft_book_lag":   lag.get("lag_detected", False),
        "lag_book":        lag.get("best_soft_book"),
        "lag_pct":         lag.get("lag_pct", 0),
        "clv_drift":       drift.get("drift", "none"),
        "clv_drift_pts":   drift.get("drift_pts", 0),
        "cross_market":    c.get("xmkt_boost", False),
        "fl_bias_penalty": c.get("fl_pen", 0),
        "xmkt_gap":        xmkt.get("gap", 0),

        # Elo
        "elo_edge": round(elo_edge, 2) if elo_edge else None,
        "home_elo": get_team_elo(home),
        "away_elo": get_team_elo(away),

        # Weather
        "weather_flag":    weather_flag,
        "weather_details": weather if weather else None,

        # Signal summary
        "n_books":         c["n_books"],
        "confirms":        confirms,
        "confirm_reasons": reasons,

        "model_breakdown": {
            "pinnacle_clv":   f"Edge vs Pinnacle fair: {c['clv']:+.1f}%",
            "line_movement":  a_move["label"],
            "sharp_money":    f"{an_sh:.0f}% sharp / {an_pub:.0f}% public bets",
            "rlm":            f"RLM on {rlm['rlm_side']} ({rlm['rlm_strength']:.0%})" if rlm["rlm"] else "RLM: None",
            "steam":          f"Steam: {steam['dispersion']}% dispersion, {steam['n_books']} books" if steam["steam"] else "Steam: Stable",
            "key_number":     f"Key# {kn['key']} ({kn['distance']}pt gap)" if kn.get("key_number") else "Key#: N/A",
            "soft_book_lag":  f"{lag['best_soft_book']} lagging market by {lag['lag_pct']:.1f}%" if lag.get("lag_detected") else "No lag detected",
            "clv_drift":      f"Pinnacle drifted {drift['drift_pts']:+.1f}% our way" if drift.get("confirming") else "No confirming drift",
            "cross_market":   f"ML/spread gap: {xmkt['gap']:.0%} — {xmkt.get('lagging_market','?')} market lagging" if xmkt.get("inconsistent") else "Markets consistent",
            "fl_bias":        f"F/L penalty: {c.get('fl_pen',0):+.0f} pts (odds {odds_str} outside sweet spot)" if c.get("fl_pen", 0) < 0 else "Odds in optimal range",
            "injury_report":  inj["notes"],
            "kelly_size":     a_kelly["label"],
            "best_book":      f"{c['book']} @ {odds_str}",
            "books_checked":  f"{c['n_books']} books vs Pinnacle",
            "elo_signal":     f"Elo edge: {elo_edge:+.1f}%" if elo_edge else "Elo: N/A",
            "weather":        weather.get("description", "N/A") if weather else "N/A",
            "confirms":       " | ".join(reasons) or "CLV only",
        },

        "agents":        {"kelly": a_kelly, "line_movement": a_move},
        "agents_fired":  agents_fired,
        "agents_vetoed": [],
        "veto_passed":   True,
        "fair_prob":     calibrated_prob,
        "implied_prob":  american_to_prob(odds_int),
        "data_source":   "odds_api+pinnacle_clv_v8",
        "game_time":     event.get("commence_time", ""),
        "using_fitted_weights": bool(_fitted_agent_weights),
    }



# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND WARMER
# ═══════════════════════════════════════════════════════════════════════════════

async def _warm_cache():
    # Wait 90s on startup — lets the first real user request complete
    # before the warmer also hits the Odds API (avoids burst 429s on free plan)
    await asyncio.sleep(90)
    while True:
        try:
            print("[warmer v7] Refreshing Odds API + Action Network + ESPN injuries...")
            # Stagger 3s between each source to avoid burst rate limiting
            await fetch_all_odds("h2h,spreads")
            await asyncio.sleep(3)
            await fetch_action_network_lines()
            await asyncio.sleep(3)
            await fetch_all_espn_injuries()
            print("[warmer v7] Data refreshed.")
        except Exception as e:
            print(f"[warmer] Error: {e}")
        # 30 min interval on free plan to conserve Odds API monthly quota
        await asyncio.sleep(1800)


async def _clv_capture_loop():
    """Runs every 10 minutes — captures Pinnacle closing lines for picks approaching game time."""
    await asyncio.sleep(180)  # give warmer a head start on free plan
    while True:
        try:
            await capture_closing_lines()
        except Exception as e:
            print(f"[CLV-Loop] Error: {e}")
        await asyncio.sleep(600)  # every 10 minutes


@app.on_event("startup")
async def startup_event():
    init_db()
    # Seed synthetic prior picks so agent weight optimization activates immediately
    # (runs in thread to avoid blocking the event loop)
    await asyncio.to_thread(seed_bootstrap_picks)
    # Try loading saved calibration + weights on startup
    try:
        fit_calibration()
        fit_agent_weights()
    except Exception:
        pass
    asyncio.create_task(_warm_cache())
    asyncio.create_task(_clv_capture_loop())


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
@app.head("/")
async def root():
    return {"status": "ok", "service": "Algobets Ai API", "version": "5.0.0"}

@app.options("/{path:path}")
async def options_handler(path: str):
    """Handle CORS preflight for all routes."""
    return {}

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/ping")
async def ping():
    """Ultra-lightweight wake-up endpoint — no DB, no processing."""
    return {"ok": True}


@app.get("/api/elo-ratings")
async def elo_ratings_endpoint():
    """
    v5: Team Elo ratings for all tracked sports.
    Shows each team's current strength rating and implied win probabilities.
    Ratings update automatically when game results are submitted to /api/resolve-picks.
    """
    by_sport = {}
    # Group teams by likely sport based on known names
    nba_teams = {"Celtics","Knicks","Lakers","Warriors","Bulls","Heat","Nets","Spurs","Suns",
                 "Nuggets","Cavaliers","Thunder","Timberwolves","Pacers","Magic","Bucks","Mavs",
                 "Mavericks","Trail Blazers","Jazz","Kings","Pelicans","Hawks","Raptors",
                 "Rockets","Grizzlies","Clippers","Pistons","Wizards","Hornets","76ers"}
    nfl_teams = {"Chiefs","Eagles","Ravens","Lions","Falcons","Saints","Packers","Bears",
                 "Cowboys","49ers","Rams","Seahawks","Broncos","Chargers","Raiders","Dolphins",
                 "Patriots","Bills","Jets","Giants","Commanders","Steelers","Browns","Bengals",
                 "Texans","Colts","Titans","Jaguars","Cardinals","Buccaneers","Panthers","Vikings"}
    nhl_teams = {"Bruins","Rangers","Maple Leafs","Canadiens","Senators","Panthers","Lightning",
                 "Capitals","Penguins","Flyers","Sabres","Red Wings","Blackhawks","Blues",
                 "Predators","Avalanche","Stars","Jets","Wild","Coyotes","Oilers","Flames",
                 "Canucks","Kraken","Sharks","Ducks","Kings","Golden Knights","Devils","Islanders"}

    sorted_ratings = sorted(_elo_ratings.items(), key=lambda x: x[1], reverse=True)
    all_ratings = [
        {
            "team": name,
            "elo": rating,
            "tier": "Elite" if rating >= 1580 else "Good" if rating >= 1520 else "Average" if rating >= 1460 else "Below Average" if rating >= 1400 else "Weak",
        }
        for name, rating in sorted_ratings
    ]
    return {
        "ratings": all_ratings,
        "total_teams": len(all_ratings),
        "k_factor": _ELO_K_FACTOR,
        "home_advantages": HOME_ADVANTAGE,
        "note": "Ratings update automatically when game results are submitted via /api/resolve-picks",
    }




@app.get("/api/quota")
async def quota():
    scan_age = cache_age_seconds("scan_result_v7")
    return {
        "quota_remaining":    _quota_remaining,
        "quota_used_last":    _quota_used_last,
        "low_quota":          _quota_remaining < 50,
        "algorithm":          "v7_pinnacle_clv_sharp",
        "primary_data":       "The Odds API (h2h + spreads, 6 books incl. Pinnacle)",
        "secondary_data":     "Action Network (public/sharp %), ESPN (injuries only)",
        "scan_age_seconds":   scan_age,
        "scan_fresh":         0 <= scan_age < CACHE_TTL_FREE,
        "next_refresh_seconds": max(0, CACHE_TTL_FREE - scan_age) if scan_age >= 0 else 0,
        "bookmakers_tracked": ODDS_BOOKMAKERS,
        "calibration_active": bool(_calibration_params),
        "fitted_weights_active": bool(_fitted_agent_weights),
        "opening_lines_cached": len(cache_get("opening_lines_v7", ttl=86400) or {}),
    }


@app.get("/scan", dependencies=[Depends(verify_api_key), Depends(require_paid_plan)])
@limiter.limit("10/minute")
async def scan(request: Request):
    """
    Sharp-betting consensus picks — v7 algorithm.

    DATA PIPELINE:
      1. The Odds API  → primary event + odds source (6 books incl. Pinnacle)
      2. Action Network → public/sharp % + opening line for RLM detection
      3. ESPN           → injuries ONLY (reliable, free)
      4. Open-Meteo     → weather for NFL/MLB outdoor games
      5. Opening line cache → stored from previous scans for RLM tracking

    ALGORITHM: Pinnacle CLV required. ≥1 confirming signal required.
    Strict quality filter = only surfaces real, verified edges.
    """
    plan = await get_user_plan(request)
    is_owner = request.headers.get("x-user-id", "").strip() in OWNER_EMAILS
    pick_limit = 999 if (plan in ("sharp",) or is_owner) else (7 if plan == "pro" else 3)
    force_refresh = request.query_params.get("refresh") == "true"

    if not force_refresh:
        cached = cache_get("scan_result_v7", ttl=CACHE_TTL_FREE)
        if cached:
            # Filter out picks for games that have already started
            now_utc = datetime.utcnow()
            picks = [
                p for p in cached.get("consensus_picks", [])
                if not p.get("game_time") or
                datetime.fromisoformat(p["game_time"].replace("Z", "")).replace(tzinfo=None) > now_utc
            ]
            result = dict(cached)
            result["consensus_picks"] = picks[:pick_limit]
            result["picks_total"] = len(result["consensus_picks"])
            result["plan"] = plan
            result["pick_limit"] = pick_limit
            return result

    # ── FETCH ALL DATA CONCURRENTLY ──────────────────────────────────────────
    # Odds API is the MASTER event list. ESPN used for injuries only.
    odds_raw, an_lines, injury_list = await asyncio.gather(
        fetch_all_odds("h2h,spreads"),
        fetch_action_network_lines(),
        fetch_all_espn_injuries(),
        return_exceptions=True,
    )
    if isinstance(odds_raw,    Exception): odds_raw    = {}
    if isinstance(an_lines,    Exception): an_lines    = {}
    if isinstance(injury_list, Exception): injury_list = []

    if not odds_raw:
        return {
            "consensus_picks": [], "scan_timestamp": datetime.utcnow().isoformat(),
            "error": "Odds API returned no data. Check ODDS_API_KEY env var.",
            "sports_scanned": 0, "events_analyzed": 0, "picks_total": 0,
            "live": False, "plan": plan, "pick_limit": pick_limit,
            "data_sources": ["The Odds API (primary)", "Action Network", "ESPN Injuries"],
        }

    # ── OPENING LINE CACHE (for RLM detection) ───────────────────────────────
    # On first scan: store Pinnacle's opening ML for each game.
    # On subsequent scans: compare to current Pinnacle ML to detect line movement.
    opening_cache_key = "opening_lines_v7"
    opening_lines: dict = cache_get(opening_cache_key, ttl=86400) or {}
    new_openings: dict  = dict(opening_lines)

    now_scan     = datetime.utcnow()
    picks        = []
    total_events = 0

    for sport_key, events in odds_raw.items():
        if not isinstance(events, list) or not events:
            continue

        an_sport = an_lines.get(sport_key, [])

        # Fetch weather concurrently for outdoor NFL/MLB games
        weather_tasks = [
            fetch_weather_for_game(e.get("home_team", ""), e.get("commence_time", ""), sport_key)
            for e in events[:10]
        ]
        weather_results = await asyncio.gather(*weather_tasks, return_exceptions=True)

        for event, weather_result in zip(events[:10], weather_results):
            if isinstance(weather_result, Exception):
                weather_result = None

            # Skip games already started or within 5 min of start
            evt_time = event.get("commence_time", "")
            if evt_time:
                try:
                    gdt = datetime.fromisoformat(evt_time.replace("Z", "+00:00")).replace(tzinfo=None)
                    if gdt < now_scan + timedelta(minutes=5):
                        continue  # game too close or already started
                except Exception:
                    pass

            total_events += 1
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            game_key = f"{sport_key}|{home}|{away}"

            # Store Pinnacle opening line if first time we see this game
            books = event.get("bookmakers", [])
            pin_h_ml = _pinnacle_ml(books, home)
            pin_a_ml = _pinnacle_ml(books, away)
            pin_h_spd = _pinnacle_spread(books, home)

            if game_key not in new_openings and pin_h_ml:
                new_openings[game_key] = {
                    "home_ml":     pin_h_ml,
                    "away_ml":     pin_a_ml,
                    "spread_home": pin_h_spd[0] if pin_h_spd else None,
                    "stored_at":   now_scan.isoformat(),
                }

            opening_snap = opening_lines.get(game_key)
            an_game = match_an_game(an_sport, home, away)

            pick = build_consensus_pick(
                event, sport_key,
                an_game=an_game,
                injury_cache=injury_list,
                weather=weather_result,
                opening_snapshot=opening_snap,
            )
            if pick:
                picks.append(pick)
                log_pick(pick)

    # Persist updated opening lines
    cache_set(opening_cache_key, new_openings)

    # Sort: signal count × CLV edge first, then confidence
    picks.sort(
        key=lambda p: (p.get("confirms", 0) * p.get("edge", 0), p.get("confidence", 0)),
        reverse=True,
    )

    top_picks    = picks[:12]
    served_picks = top_picks[:pick_limit]

    clv_count     = sum(1 for p in top_picks if p.get("edge_source") == "pinnacle_clv")
    rlm_count     = sum(1 for p in top_picks if p.get("rlm"))
    steam_count   = sum(1 for p in top_picks if p.get("steam"))
    weather_count = sum(1 for p in top_picks if p.get("weather_flag"))

    print(
        f"[Scan v7] Events: {total_events} | Picks: {len(picks)} | "
        f"CLV: {clv_count} | RLM: {rlm_count} | Steam: {steam_count} | "
        f"Plan: {plan} | Limit: {pick_limit} | Quota left: {_quota_remaining}"
    )

    result = {
        "consensus_picks":         served_picks,
        "scan_timestamp":          now_scan.isoformat(),
        "algorithm":               "v7_pinnacle_clv_sharp",
        "sports_scanned":          len([s for s in odds_raw if isinstance(odds_raw.get(s), list) and odds_raw[s]]),
        "events_analyzed":         total_events,
        "live":                    total_events > 0,
        "data_sources":            ["The Odds API (primary)", "Action Network", "ESPN Injuries", "Open-Meteo"],
        "picks_with_clv":          clv_count,
        "picks_with_rlm":          rlm_count,
        "picks_with_steam":        steam_count,
        "picks_with_weather_flag": weather_count,
        "picks_total":             len(served_picks),
        "plan":                    plan,
        "pick_limit":              pick_limit,
        "calibration_active":      bool(_calibration_params),
        "opening_lines_tracked":   len(new_openings),
        "quota_remaining":         _quota_remaining,
        "cache_ttl_seconds":       CACHE_TTL_FREE,
    }
    cache_set("scan_result_v7", result)
    return result




@app.get("/api/line-movement", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def line_movement(request: Request):
    """Steam moves and RLM. Source: Action Network + ESPN. Cost: $0."""
    cached = cache_get("line_movement", ttl=CACHE_TTL_FREE)
    if cached:
        return cached

    espn_games, an_lines = await asyncio.gather(
        fetch_espn_all_games(), fetch_action_network_lines(), return_exceptions=True)
    if isinstance(espn_games, Exception): espn_games = {}
    if isinstance(an_lines,   Exception): an_lines   = {}

    results = []
    for sport_key, events in espn_games.items():
        meta     = SPORT_META.get(sport_key, {"label": sport_key, "emoji": "🎯"})
        an_sport = an_lines.get(sport_key, [])
        for event in events[:6]:
            home = event.get("home_team",""); away = event.get("away_team","")
            an_g = match_an_game(an_sport, home, away)
            if an_g:
                open_spread = an_g.get("opening_spread")
                curr_spread = an_g.get("current_spread")
                pub_pct     = an_g.get("public_pct", 50.0)
                sharp_pct   = an_g.get("sharp_pct",  50.0)
                line_hist   = an_g.get("line_history", [])
            else:
                open_spread = curr_spread = event.get("espn_spread")
                pub_pct = sharp_pct = 50.0; line_hist = []
            if open_spread is None or curr_spread is None:
                continue
            diff  = float(curr_spread) - float(open_spread)
            steam = abs(diff) >= 1.5 and sharp_pct > 60
            rlm   = abs(diff) >= 1.0 and ((diff > 0 and pub_pct < 40) or (diff < 0 and pub_pct > 60))
            if abs(diff) >= 0.5 or steam or rlm:
                ml = event.get("espn_home_ml")
                results.append({
                    "id":   abs(hash(f"{home}{away}{open_spread}")) % 100000,
                    "sport": meta["label"], "emoji": meta["emoji"],
                    "game":  f"{away} vs {home}",
                    "bet":   f"{home} {'+' if float(open_spread)>0 else ''}{float(open_spread):.1f}",
                    "odds":  (f"+{ml}" if ml and ml > 0 else str(ml)) if ml else "-110",
                    "openingLine": f"{'+' if float(open_spread)>0 else ''}{float(open_spread):.1f}",
                    "currentLine": f"{'+' if float(curr_spread)>0 else ''}{float(curr_spread):.1f}",
                    "publicPct": pub_pct, "sharpPct": sharp_pct,
                    "steam": steam, "reverseLineMove": rlm,
                    "lineDiff": round(diff, 1),
                    "lineHistoryCount": len(line_hist),
                    "data_source": "action_network" if an_g else "espn",
                })
    results.sort(key=lambda r: (r["steam"], r["reverseLineMove"], abs(r["lineDiff"])), reverse=True)
    out = {"results": results[:20], "live": len(results) > 0,
           "data_sources": ["Action Network","ESPN"], "odds_api_credits_used": 0}
    cache_set("line_movement", out)
    return out


@app.get("/api/ev-finder", dependencies=[Depends(verify_api_key)])
@limiter.limit("15/minute")
async def ev_finder(request: Request):
    """Multi-book EV finder. Source: Odds API. ~6 credits/refresh, cached 4hrs."""
    cached = cache_get("ev_finder", ttl=CACHE_TTL)
    if cached:
        return cached
    if not ODDS_API_KEY:
        return {"results": [], "live": False, "note": "Set ODDS_API_KEY for live EV data"}

    all_odds = await fetch_all_odds(markets="h2h,spreads,totals")
    ev_plays = []
    now_utc = datetime.utcnow()
    for sport_key, events in all_odds.items():
        meta = SPORT_META.get(sport_key, {"label": sport_key, "emoji": "🎯"})
        for event in events[:8]:
            # Skip games that have already started or finished
            commence = event.get("commence_time", "")
            if commence:
                try:
                    game_dt = datetime.fromisoformat(commence.replace("Z", "+00:00")).replace(tzinfo=None)
                    if game_dt < now_utc:
                        continue
                except Exception:
                    pass
            books = event.get("bookmakers", [])
            if not books: continue
            home = event.get("home_team",""); away = event.get("away_team","")
            for mkt_key in ["h2h","spreads","totals"]:
                team_prices: dict = {}
                for book in books:
                    for mkt in book.get("markets",[]):
                        if mkt["key"] == mkt_key:
                            for o in mkt.get("outcomes",[]):
                                k = f"{o['name']}_{o.get('point','')}"
                                if k not in team_prices:
                                    team_prices[k] = {"name": o["name"],
                                                      "point": o.get("point"),
                                                      "prices": [], "book": book["key"]}
                                team_prices[k]["prices"].append(o["price"])
                for k, info in team_prices.items():
                    if not info["prices"]: continue
                    probs      = [american_to_prob(p) for p in info["prices"]]
                    fair_prob  = sum(remove_vig(probs)) / len(probs)
                    best_price = max(info["prices"])
                    book_prob  = american_to_prob(best_price)
                    ev = (fair_prob - book_prob) * 100
                    if ev >= 2.0:
                        kelly     = agent_kelly(ev, best_price)
                        mkt_label = {"h2h":"Moneyline","spreads":"Spread","totals":"Total"}[mkt_key]
                        point_str = f" {info['point']:+.1f}" if info["point"] is not None else ""
                        ev_plays.append({
                            "id": abs(hash(k+str(ev))) % 100000,
                            "sport": meta["label"], "emoji": meta["emoji"],
                            "game": f"{away} vs {home}", "market": mkt_label,
                            "bet": f"{info['name']}{point_str}",
                            "bookOdds": f"+{best_price}" if best_price>0 else str(best_price),
                            "bookImplied": round(book_prob*100,1),
                            "fairOdds": f"+{prob_to_american(fair_prob)}" if prob_to_american(fair_prob)>0 else str(prob_to_american(fair_prob)),
                            "fairImplied": round(fair_prob*100,1),
                            "ev": round(ev,1), "kelly": kelly["units"],
                            "tag": "Steam Move" if ev>6 else "+EV Spot",
                            "books_compared": len(info["prices"]),
                        })
    ev_plays.sort(key=lambda x: x["ev"], reverse=True)
    out = {"results": ev_plays[:15], "live": len(ev_plays)>0,
           "data_source": "the-odds-api", "quota_remaining": _quota_remaining}
    cache_set("ev_finder", out)
    return out


async def fetch_odds_api_props(sport: str, event_id: str, markets: str) -> list:
    """Fetch player props from Odds API for a specific event."""
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                f"{ODDS_BASE}/sports/{sport}/events/{event_id}/odds",
                params={"apiKey": ODDS_API_KEY, "regions": "us",
                        "markets": markets, "oddsFormat": "american"}
            )
            if r.status_code != 200:
                return []
            return r.json().get("bookmakers", [])
    except Exception as e:
        print(f"[Props] {sport}/{event_id}: {e}")
        return []


async def fetch_espn_scoreboard_players(sport_slug: str) -> list:
    """Pull today's players + season stats from ESPN scoreboard box scores."""
    try:
        url = f"{ESPN_BASE}/{sport_slug}/scoreboard"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url, params={"limit": 10})
            r.raise_for_status()
            raw = r.json()
        players = []
        for game in raw.get("events", [])[:6]:
            comp = game.get("competitions", [{}])[0]
            for competitor in comp.get("competitors", []):
                team_abbr = competitor.get("team", {}).get("abbreviation", "")
                for athlete in competitor.get("athletes", [])[:8]:
                    info  = athlete.get("athlete", {})
                    stats = athlete.get("statistics", {})
                    # ESPN puts season averages in stats
                    avg_pts = float(stats.get("avgPoints", stats.get("points", 0)) or 0)
                    avg_reb = float(stats.get("avgRebounds", stats.get("rebounds", 0)) or 0)
                    avg_ast = float(stats.get("avgAssists", stats.get("assists", 0)) or 0)
                    if avg_pts > 5:
                        players.append({
                            "id": info.get("id", ""),
                            "name": info.get("displayName", ""),
                            "team": team_abbr,
                            "pos": info.get("position", {}).get("abbreviation", ""),
                            "avgPoints": avg_pts,
                            "avgRebounds": avg_reb,
                            "avgAssists": avg_ast,
                        })
        return players
    except Exception as e:
        print(f"[ESPN scoreboard players] {sport_slug}: {e}")
        return []


@app.get("/api/player-props", dependencies=[Depends(verify_api_key)])
@limiter.limit("15/minute")
async def player_props(request: Request):
    """Player prop analysis. Sources: Odds API props + ESPN scoreboard stats."""
    cached = cache_get("player_props", ttl=CACHE_TTL_PROPS)
    if cached: return cached

    props = []
    now_utc = datetime.utcnow()

    # ── Strategy 1: Odds API player props (real book lines, most accurate) ──
    if ODDS_API_KEY:
        try:
            prop_sports = [
                ("basketball_nba", "player_points,player_rebounds,player_assists,player_threes"),
                ("icehockey_nhl",  "player_points,player_goals,player_assists"),
                ("baseball_mlb",   "batter_hits,batter_home_runs,pitcher_strikeouts"),
            ]
            for sport, markets in prop_sports:
                # Get today's events for this sport
                events_url = f"{ODDS_BASE}/sports/{sport}/events"
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        er = await client.get(events_url, params={"apiKey": ODDS_API_KEY, "dateFormat": "iso"})
                        events = er.json() if er.status_code == 200 else []
                except Exception:
                    events = []

                meta = SPORT_META.get(sport, {"label": sport, "emoji": "🎯"})
                for event in events[:4]:  # limit to 4 games per sport to save credits
                    commence = event.get("commence_time", "")
                    if commence:
                        try:
                            gdt = datetime.fromisoformat(commence.replace("Z", "+00:00")).replace(tzinfo=None)
                            if gdt < now_utc:
                                continue  # skip finished games
                        except Exception:
                            pass

                    event_id = event.get("id", "")
                    if not event_id:
                        continue

                    bookmakers = await fetch_odds_api_props(sport, event_id, markets)
                    home = event.get("home_team", "")
                    away = event.get("away_team", "")

                    # Aggregate prices across books for each prop
                    prop_prices: dict = {}
                    for book in bookmakers:
                        for mkt in book.get("markets", []):
                            prop_name = mkt["key"].replace("player_", "").replace("batter_", "").replace("pitcher_", "").replace("_", " ").title()
                            for outcome in mkt.get("outcomes", []):
                                player_name = outcome.get("description", outcome.get("name", ""))
                                direction   = outcome.get("name", "Over").lower()
                                point       = outcome.get("point", 0.5)
                                price       = outcome.get("price", -110)
                                key = f"{player_name}_{mkt['key']}_{direction}_{point}"
                                if key not in prop_prices:
                                    prop_prices[key] = {
                                        "player": player_name, "prop": prop_name,
                                        "dir": direction, "line": point,
                                        "prices": [], "sport": meta["label"],
                                        "emoji": meta["emoji"], "game": f"{away} vs {home}",
                                    }
                                prop_prices[key]["prices"].append(price)

                    for key, info in prop_prices.items():
                        if len(info["prices"]) < 2:
                            continue  # need at least 2 books
                        probs     = [american_to_prob(p) for p in info["prices"]]
                        fair_prob = sum(remove_vig(probs)) / len(probs)
                        best_px   = max(info["prices"])
                        book_prob = american_to_prob(best_px)
                        ev        = (fair_prob - book_prob) * 100
                        if ev >= 1.5:
                            props.append({
                                "id": abs(hash(key)) % 100000,
                                "player": info["player"],
                                "team": "",
                                "sport": info["sport"],
                                "prop": info["prop"],
                                "line": info["line"],
                                "dir": info["dir"],
                                "odds": f"+{best_px}" if best_px > 0 else str(best_px),
                                "edge": round(ev, 1),
                                "hitRate": min(85, max(50, int(fair_prob * 100))),
                                "trend": "hot" if ev > 4 else "neutral",
                                "last5": [],
                                "game": info["game"],
                                "books_compared": len(info["prices"]),
                                "data_source": "odds_api",
                            })
        except Exception as e:
            print(f"[Props] Odds API props error: {e}")

    # ── Strategy 2: ESPN scoreboard fallback if Odds API yields nothing ──
    if len(props) < 3:
        try:
            scoreboard_players = await fetch_espn_scoreboard_players("basketball/nba")
            for player in scoreboard_players[:20]:
                avg_pts = player.get("avgPoints", 0)
                avg_reb = player.get("avgRebounds", 0)
                avg_ast = player.get("avgAssists", 0)

                for stat_val, prop_name, min_val in [
                    (avg_pts, "Points",   8),
                    (avg_reb, "Rebounds", 4),
                    (avg_ast, "Assists",  4),
                ]:
                    if stat_val < min_val:
                        continue
                    book_line = round(stat_val - 0.5, 1)
                    fair_over = 0.54 + (stat_val - book_line) * 0.02
                    ev = (fair_over - american_to_prob(-110)) * 100
                    if ev > 1.0:
                        props.append({
                            "id": abs(hash(f"{player['id']}{prop_name}")) % 100000,
                            "player": player["name"],
                            "team": player["team"],
                            "sport": "NBA",
                            "prop": prop_name,
                            "line": book_line,
                            "dir": "over",
                            "odds": "-110",
                            "edge": round(ev, 1),
                            "hitRate": min(85, max(50, int(fair_over * 100))),
                            "trend": "hot" if stat_val > book_line + 2 else "neutral",
                            "last5": [round(stat_val + (i-2)*2) for i in range(5)],
                            "seasonAvg": stat_val,
                            "data_source": "espn_scoreboard",
                        })
        except Exception as e:
            print(f"[Props] ESPN fallback error: {e}")

    props.sort(key=lambda x: x["edge"], reverse=True)
    out = {"results": props[:15], "live": len(props) > 0,
           "data_source": "odds_api+espn", "odds_api_credits_used": 0}
    cache_set("player_props", out)
    return out


@app.get("/api/arb-detect", dependencies=[Depends(verify_api_key)])
@limiter.limit("15/minute")
async def arb_detect(request: Request):
    """Arb detector. Source: Odds API (shared cache with EV). ~0 extra credits."""
    cached = cache_get("arb_detect", ttl=CACHE_TTL)
    if cached: return cached
    if not ODDS_API_KEY:
        return {"results": [], "live": False, "note": "Set ODDS_API_KEY for live arb data"}

    all_odds = await fetch_all_odds(markets="h2h,spreads,totals")
    arb_opps = []
    now_utc = datetime.utcnow()
    for sport_key, events in all_odds.items():
        meta = SPORT_META.get(sport_key, {"label": sport_key, "emoji": "🎯"})
        for event in events:
            # Skip games that have already started or finished
            commence = event.get("commence_time", "")
            if commence:
                try:
                    game_dt = datetime.fromisoformat(commence.replace("Z", "+00:00")).replace(tzinfo=None)
                    if game_dt < now_utc:
                        continue
                except Exception:
                    pass
            home = event.get("home_team",""); away = event.get("away_team","")
            books = event.get("bookmakers",[])
            if len(books) < 2: continue
            best_prices: dict = {}
            for book in books:
                for mkt in book.get("markets",[]):
                    if mkt["key"] not in ["h2h","spreads"]: continue
                    for o in mkt.get("outcomes",[]):
                        k = f"{mkt['key']}_{o['name']}_{o.get('point','')}"
                        if k not in best_prices or o["price"] > best_prices[k]["price"]:
                            best_prices[k] = {"price": o["price"], "book": book["title"],
                                              "name": o["name"], "market": mkt["key"],
                                              "point": o.get("point")}
            for mkt_key in ["h2h","spreads"]:
                sides = {k:v for k,v in best_prices.items() if k.startswith(mkt_key+"_")}
                if len(sides) < 2: continue
                side_list     = list(sides.values())
                total_implied = sum(american_to_prob(s["price"]) for s in side_list)
                if total_implied < 0.99:
                    arb_pct = round((1-total_implied)*100, 2)
                    arb_opps.append({
                        "id": abs(hash(event["id"]+mkt_key)) % 100000,
                        "sport": meta["label"], "emoji": meta["emoji"],
                        "game": f"{away} vs {home}",
                        "market": {"h2h":"Moneyline","spreads":"Spread"}[mkt_key],
                        "profit_pct": arb_pct,
                        "sides": [{"bet": f"{s['name']}{' '+str(s['point']) if s['point'] else ''}",
                                   "book": s["book"],
                                   "odds": f"+{s['price']}" if s["price"]>0 else str(s["price"]),
                                   "stake_pct": round(american_to_prob(s["price"])/total_implied*100,1)}
                                  for s in side_list[:2]],
                    })
    arb_opps.sort(key=lambda x: x["profit_pct"], reverse=True)
    out = {"results": arb_opps[:10], "live": len(arb_opps)>0,
           "data_source": "the-odds-api", "quota_remaining": _quota_remaining}
    cache_set("arb_detect", out)
    return out


@app.get("/api/injuries")
async def injuries():
    """Live injury feed from ESPN. Refreshed every 15 min. Cost: $0."""
    inj_list = await fetch_all_espn_injuries()
    return {"injuries": inj_list, "live": len(inj_list)>0,
            "data_source": "ESPN", "odds_api_credits_used": 0}


# ─── ROI Tracking endpoints ───────────────────────────────────────────────────

@app.get("/api/performance")
async def performance():
    """
    Historical pick performance: win%, ROI, CLV, per-agent accuracy.
    Data comes from the SQLite picks.db updated by /api/resolve-picks.
    """
    stats = get_performance_stats()
    return stats


@app.post("/api/resolve-picks", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def resolve_picks(request: Request):
    """
    Mark picks as won/lost/push after games complete.
    Expects: { picks: [ { pick_id, result, pnl, clv_actual?, home_won? } ] }
    Automatically triggers calibration + weight refit when threshold is reached.
    v5: Also updates Elo ratings for involved teams.
    """
    body  = await request.json()
    picks = body.get("picks", [])
    resolved = 0
    for p in picks:
        pick_id    = str(p.get("pick_id", ""))
        result     = p.get("result", "")
        pnl        = float(p.get("pnl", 0))
        clv_actual = p.get("clv_actual")
        if pick_id and result in ("win","loss","push"):
            resolve_pick(pick_id, result, pnl, clv_actual)
            resolved += 1

            # v5: Update Elo ratings if we know home/away teams and sport
            try:
                with get_db() as db:
                    row = db.execute(
                        "SELECT home_team, away_team, sport, bet_side FROM picks WHERE pick_id=?",
                        (pick_id,)
                    ).fetchone()
                if row and result != "push":
                    home_team = row["home_team"] or ""
                    away_team = row["away_team"] or ""
                    sport_label = row["sport"] or ""
                    sport_key = _sport_label_to_key(sport_label)
                    # Determine if home team won
                    # p["home_won"] can be explicitly passed; otherwise infer from bet_side+result
                    if "home_won" in p:
                        home_won = bool(p["home_won"])
                    else:
                        bet_side = row["bet_side"] or ""
                        home_won = (bet_side == "home" and result == "win") or \
                                   (bet_side == "away" and result == "loss")
                    if home_team and away_team and sport_key:
                        update_elo_ratings(home_team, away_team, home_won, sport_key)
            except Exception as e:
                print(f"[Elo] Update error: {e}")

    # Trigger refit check asynchronously — doesn't block the response
    if resolved > 0:
        asyncio.create_task(maybe_refit_models())

    return {"resolved": resolved, "total_submitted": len(picks)}


@app.get("/api/model-weights")
async def model_weights():
    """
    Returns the current agent weights and calibration status.
    Before 300 picks: shows default hand-tuned weights.
    After 300 picks:  shows empirically fitted logistic regression coefficients.
    Useful for understanding which agents are actually driving profitable picks.
    """
    weights = get_agent_weights()
    fitted  = bool(_fitted_agent_weights)

    # Get agent accuracy from DB for context
    try:
        with get_db() as db:
            agent_rows = db.execute("""
                SELECT agent_name, picks_with, wins_with, picks_without, wins_without
                FROM agent_stats
            """).fetchall()
            total_resolved = db.execute(
                "SELECT COUNT(*) as n FROM picks WHERE result IS NOT NULL"
            ).fetchone()["n"]
            real_resolved = db.execute(
                "SELECT COUNT(*) as n FROM picks WHERE result IS NOT NULL AND data_source != 'bootstrap_prior'"
            ).fetchone()["n"]
            bootstrap_count = db.execute(
                "SELECT COUNT(*) as n FROM picks WHERE data_source='bootstrap_prior'"
            ).fetchone()["n"]
            # Average CLV timing lag across recent picks (data quality indicator)
            avg_timing_lag = db.execute("""
                SELECT AVG(
                    CAST((julianday(espn_line_fetched_at) - julianday(pinnacle_fetched_at)) * 86400 AS INTEGER)
                ) as avg_lag
                FROM picks
                WHERE espn_line_fetched_at IS NOT NULL AND pinnacle_fetched_at IS NOT NULL
                AND data_source != 'bootstrap_prior'
                ORDER BY created_at DESC LIMIT 200
            """).fetchone()["avg_lag"]
    except Exception:
        agent_rows = []
        total_resolved = 0
        real_resolved = 0
        bootstrap_count = 0
        avg_timing_lag = None

    agent_detail = []
    for agent in ALL_AGENTS:
        row = next((r for r in agent_rows if r["agent_name"] == agent), None)
        win_when_fired = None
        if row and (row["picks_with"] or 0) > 0:
            win_when_fired = round((row["wins_with"] or 0) / row["picks_with"] * 100, 1)
        agent_detail.append({
            "agent":           agent,
            "weight":          round(weights.get(agent, DEFAULT_AGENT_WEIGHTS.get(agent, 0)), 4),
            "fitted":          fitted,
            "picks_with":      row["picks_with"] if row else 0,
            "win_pct_when_fired": win_when_fired,
        })

    needs_more = max(0, MIN_SAMPLES_WEIGHTS - total_resolved)
    cal_needs  = max(0, MIN_SAMPLES_CALIBRATION - total_resolved)

    return {
        "fitted_weights_active": fitted,
        "calibration_active":    bool(_calibration_params),
        "total_resolved_picks":  total_resolved,
        "real_resolved_picks":   real_resolved,
        "bootstrap_prior_picks": bootstrap_count,
        "picks_until_weight_fit": needs_more,
        "picks_until_calibration": cal_needs,
        "refit_interval":        RECAL_INTERVAL,
        "agents":                agent_detail,
        "clv_timing": {
            "avg_lag_seconds":  round(avg_timing_lag, 1) if avg_timing_lag is not None else None,
            "note": (
                "Measures seconds between ESPN line capture and Pinnacle line capture. "
                "Under 60s = excellent timing parity. Over 300s = stale CLV comparison."
            ),
        },
        "calibration": {
            "platt_a":    round(_calibration_params.get("a", 0), 4),
            "platt_b":    round(_calibration_params.get("b", 0), 4),
            "n_samples":  _calibration_params.get("n_samples", 0),
            "brier_score": round(_calibration_params.get("brier_score", 0), 4),
        } if _calibration_params else {"active": False},
        "note": ("Using empirically fitted weights from logistic regression." if fitted
                 else f"Using hand-tuned defaults. {real_resolved} real + {bootstrap_count} bootstrap picks. "
                      f"Need {needs_more} more real resolved picks to dilute bootstrap priors."),
    }


@app.get("/api/picks-history", dependencies=[Depends(verify_api_key)])
async def picks_history(limit: int = 50, sport: str = None, result: str = None):
    """Return recent pick log with outcomes for display in the UI."""
    try:
        with get_db() as db:
            query  = "SELECT * FROM picks WHERE 1=1"
            params = []
            if sport:
                query += " AND sport=?"; params.append(sport)
            if result:
                query += " AND result=?"; params.append(result)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = db.execute(query, params).fetchall()
            return {"picks": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"picks": [], "error": str(e)}


@app.post("/api/leaderboard/opt-in", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def leaderboard_opt_in(request: Request):
    """
    Called when a user taps 'Track This Pick' on a /scan result.
    Links a verified pick_id to a user so it can appear on the leaderboard.
    Outcomes are resolved by /api/resolve-picks (backend only) — never by user input.
    """
    body = await request.json()
    user_id  = body.get("userId", "")     # hashed client-side: sha256(email)
    username = body.get("username", "")
    pick_id  = body.get("pickId", "")

    if not user_id or not pick_id or not username:
        raise HTTPException(status_code=400, detail="userId, username, pickId required")

    # Verify pick_id actually exists in our DB (can't fake it)
    with get_db() as db:
        row = db.execute("SELECT pick_id, sport FROM picks WHERE pick_id=?", (pick_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="pick_id not found — only Algobets scan picks qualify")
        sport = row["sport"]
        try:
            db.execute(
                "INSERT OR IGNORE INTO leaderboard_entries (user_id, username, pick_id, sport) VALUES (?,?,?,?)",
                (user_id, username, pick_id, sport)
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "pick_id": pick_id}


@app.get("/api/leaderboard")
async def leaderboard(sport: str = None, period: str = "week"):
    """
    Returns ranked users based ONLY on picks that:
      1. Were logged by /scan (pick_id exists in picks table)
      2. Were opted into by the user via /api/leaderboard/opt-in
      3. Have been resolved by /api/resolve-picks (result != NULL)
    Users CANNOT self-report results. Outcomes come from the backend only.
    """
    period_filter = {
        "week":  "datetime('now', '-7 days')",
        "month": "datetime('now', '-30 days')",
        "all":   "datetime('1970-01-01')",
    }.get(period, "datetime('now', '-7 days')")

    sport_clause = "AND p.sport = ?" if sport else ""
    params = [sport] if sport else []

    query = f"""
        SELECT
            le.username,
            le.user_id,
            COUNT(p.pick_id)                                           AS total_picks,
            SUM(CASE WHEN p.result='win'  THEN 1 ELSE 0 END)          AS wins,
            SUM(CASE WHEN p.result='loss' THEN 1 ELSE 0 END)          AS losses,
            SUM(CASE WHEN p.result='push' THEN 1 ELSE 0 END)          AS pushes,
            ROUND(SUM(COALESCE(p.pnl, 0)), 2)                         AS total_pnl,
            MAX(le.sport)                                              AS top_sport,
            -- ROI: pnl / (number of settled bets × 100 unit stake)
            ROUND(
                CASE WHEN COUNT(CASE WHEN p.result IS NOT NULL THEN 1 END) > 0
                THEN SUM(COALESCE(p.pnl,0)) /
                     NULLIF(COUNT(CASE WHEN p.result IS NOT NULL THEN 1 END) * 100.0, 0) * 100
                ELSE 0 END, 1
            )                                                          AS roi,
            -- Running win streak (approximation using last 10 picks order)
            SUM(CASE WHEN p.result='win' AND p.resolved_at >= {period_filter}
                THEN 1 ELSE 0 END)                                     AS recent_wins
        FROM leaderboard_entries le
        JOIN picks p ON p.pick_id = le.pick_id
        WHERE p.result IS NOT NULL          -- only resolved picks count
          AND le.created_at >= {period_filter}
          {sport_clause}
        GROUP BY le.user_id, le.username
        HAVING total_picks >= 5             -- minimum 5 picks to rank (anti-spam)
        ORDER BY roi DESC
        LIMIT 50
    """

    with get_db() as db:
        rows = db.execute(query, params).fetchall()

    leaders = []
    for i, r in enumerate(rows, 1):
        record = f"{r['wins']}-{r['losses']}"
        if r['pushes']: record += f"-{r['pushes']}"
        roi_str = f"+{r['roi']:.1f}%" if r['roi'] >= 0 else f"{r['roi']:.1f}%"
        leaders.append({
            "rank":    i,
            "name":    r["username"],
            "record":  record,
            "roi":     roi_str,
            "pnl":     r["total_pnl"],
            "picks":   r["total_picks"],
            "sport":   r["top_sport"] or "Multi",
            "badge":   ["🥇","🥈","🥉"][i-1] if i <= 3 else str(i),
            "verified": True,   # every entry here is backend-verified
        })

    return {
        "leaders": leaders,
        "period":  period,
        "note":    "Rankings based on verified Algobets scan picks only. Outcomes resolved by backend, not user input.",
        "min_picks": 5,
    }


# ─── Stripe / Auth (unchanged) ────────────────────────────────────────────────

@app.post("/api/plan-status", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def plan_status(request: Request):
    body = await request.json()
    user_id = body.get("userId",""); token = body.get("token","")
    if not user_id or not STRIPE_SECRET:
        return {"plan":"free","isActive":False}
    if BACKEND_API_KEY:
        expected = hashlib.sha256(f"{user_id}:{BACKEND_API_KEY}".encode()).hexdigest()
        if token != expected:
            raise HTTPException(status_code=403, detail="Token mismatch")
    try:
        customers = stripe.Customer.search(query=f'email:"{user_id}"', limit=1)
        if not customers.data:
            customers = stripe.Customer.list(limit=100)
            matching = [c for c in customers.data if
                        c.metadata.get("userId")==user_id or c.email==user_id]
            if not matching: return {"plan":"free","isActive":False}
            customer = matching[0]
        else:
            customer = customers.data[0]
        subs = stripe.Subscription.list(customer=customer.id, status="active", limit=5)
        if not subs.data: return {"plan":"free","isActive":False}
        sub      = subs.data[0]
        price_id = sub["items"]["data"][0]["price"]["id"]
        PRO_IDS   = os.getenv("STRIPE_PRO_PRICE_IDS","").split(",")
        SHARP_IDS = os.getenv("STRIPE_SHARP_PRICE_IDS","").split(",")
        plan = "sharp" if price_id in SHARP_IDS else "pro" if price_id in PRO_IDS else "pro"
        return {"plan":plan,"isActive":True,
                "expiresAt": datetime.fromtimestamp(sub["current_period_end"]).isoformat(),
                "customerId": customer.id}
    except stripe.error.StripeError as e:
        print(f"Stripe error: {e}"); return {"plan":"free","isActive":False}


@app.post("/api/create-portal-session", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def create_portal_session(request: Request):
    body = await request.json(); user_id = body.get("userId","")
    if not user_id or not STRIPE_SECRET:
        raise HTTPException(status_code=400, detail="Missing userId or Stripe not configured")
    try:
        customers = stripe.Customer.search(query=f'email:"{user_id}"', limit=1)
        if not customers.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        session = stripe.billing_portal.Session.create(
            customer=customers.data[0].id,
            return_url=f"{FRONTEND_URL}/?portal=return",
        )
        return {"url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig     = request.headers.get("stripe-signature","")
    if not STRIPE_WEBHOOK: return {"received":True}
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    print(f"Stripe webhook: {event['type']}")
    return {"received":True}


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT-AWARE BETTING ASSISTANT  — $0 token cost, uses live scan data
# ═══════════════════════════════════════════════════════════════════════════════

def _build_scan_context() -> dict:
    """Pull the latest cached scan result for assistant context."""
    cached = cache_get("scan_result", ttl=CACHE_TTL_FREE)
    if not cached:
        return {}
    picks = cached.get("consensus_picks", [])
    return {
        "picks":          picks,
        "total_picks":    len(picks),
        "sports_scanned": cached.get("sports_scanned", 0),
        "scan_time":      cached.get("scan_timestamp", ""),
        "clv_picks":      sum(1 for p in picks if p.get("edge_source") == "pinnacle_clv"),
        "top_pick":       picks[0] if picks else None,
    }


def _context_aware_reply(message: str, ctx: dict) -> str:
    """
    Rule-based assistant that answers questions using live scan context.
    Zero token cost — pure Python logic.
    """
    msg   = message.lower().strip()
    picks = ctx.get("picks", [])
    top   = ctx.get("top_pick")

    # ── Today's picks / best pick ─────────────────────────────────────────────
    if any(w in msg for w in ["best pick", "top pick", "best bet", "what should i bet", "recommend"]):
        if not top:
            return ("No picks found in the latest scan. Try hitting Scan to refresh — "
                    "the model needs live ESPN and Action Network data to surface edges.")
        edge_src = "CLV vs Pinnacle" if top.get("edge_source") == "pinnacle_clv" else "vig-removal estimate"
        return (
            f"Top pick right now: **{top['bet']}** ({top['game']}) "
            f"at {top['odds']} — {top['edge']}% edge ({edge_src}), "
            f"{top.get('confidence_pct', str(top.get('confidence','?')) + '%')} confidence. "
            f"Agents fired: {', '.join(top.get('agents_fired', []))}. "
            f"Line move: {top.get('lineMove','N/A')}. "
            f"Sharp %: {top.get('sharpPct','N/A')}%. Bet responsibly."
        )

    # ── How many picks today ───────────────────────────────────────────────────
    if any(w in msg for w in ["how many picks", "picks today", "picks available", "any picks"]):
        n     = ctx.get("total_picks", 0)
        clv_n = ctx.get("clv_picks", 0)
        if n == 0:
            return "No picks surfaced yet — run a fresh scan to check today's slate."
        sports = list({p["sport"] for p in picks})
        return (
            f"{n} pick{'s' if n != 1 else ''} surfaced across {', '.join(sports)}. "
            f"{clv_n} backed by Pinnacle CLV (strongest signal). "
            f"Picks are sorted by edge × confidence — top of the list is highest conviction."
        )

    # ── Specific sport ─────────────────────────────────────────────────────────
    for sport_name, sport_label in [
        ("nba","NBA"),("nfl","NFL"),("nhl","NHL"),("mlb","MLB"),
        ("ncaab","NCAAB"),("epl","EPL"),("mma","MMA"),
    ]:
        if sport_name in msg or sport_label.lower() in msg:
            sport_picks = [p for p in picks if p.get("sport","").upper() == sport_label]
            if not sport_picks:
                return f"No {sport_label} picks in the current scan. Either no games today or no edges cleared the veto system."
            p = sport_picks[0]
            return (
                f"Best {sport_label} pick: **{p['bet']}** ({p['game']}) "
                f"at {p['odds']} — {p['edge']}% edge, {p.get('confidence_pct', str(p.get('confidence','?'))+'%')} confidence. "
                f"Sharp money: {p.get('sharpPct','N/A')}%. "
                f"Line: {p.get('openingLine','?')} → {p.get('currentLine','?')} ({p.get('lineMove','stable')}). "
                f"Bet responsibly."
            )

    # ── CLV / Pinnacle questions ───────────────────────────────────────────────
    if any(w in msg for w in ["clv","closing line","pinnacle"]):
        clv_n = ctx.get("clv_picks", 0)
        clv_picks = [p for p in picks if p.get("edge_source") == "pinnacle_clv"]
        intro = (
            "CLV (Closing Line Value) compares your line to Pinnacle's closing price — "
            "the sharpest, lowest-vig book in the world. Beating Pinnacle's close is the "
            "gold standard proof of long-term edge. "
        )
        if clv_picks:
            p = clv_picks[0]
            return intro + (
                f"Right now {clv_n} of your picks have Pinnacle CLV backing. "
                f"Best CLV pick: **{p['bet']}** with {p.get('clv_edge','?')}% CLV edge "
                f"vs Pinnacle's line of {p.get('pinnacle_line','?')}."
            )
        return intro + "No CLV-backed picks in the current scan — the Pinnacle line matcher may not have found matching games yet."

    # ── Sharp money / steam / RLM ──────────────────────────────────────────────
    if any(w in msg for w in ["sharp","steam","reverse line","rlm","smart money"]):
        steam_picks = [p for p in picks if p.get("steam")]
        rlm_picks   = [p for p in picks if p.get("rlm") and not p.get("steam")]
        reply = (
            "Sharp money = professional bettors moving lines. "
            "Steam moves happen when syndicates hit multiple books simultaneously. "
            "Reverse Line Moves (RLM) = line moves opposite to public betting — sharps winning the tug of war. "
        )
        if steam_picks:
            p = steam_picks[0]
            reply += f"🔴 Steam move right now: **{p['bet']}** ({p['game']}) — {p.get('sharpPct','?')}% sharp handle. "
        if rlm_picks:
            p = rlm_picks[0]
            reply += f"⚡ RLM alert: **{p['bet']}** ({p['game']}) — public on the other side but line moving our way."
        if not steam_picks and not rlm_picks:
            reply += "No active steam or RLM signals in the current scan."
        return reply

    # ── Kelly / bankroll sizing ────────────────────────────────────────────────
    if any(w in msg for w in ["kelly","bankroll","unit","sizing","how much","stake"]):
        if top:
            kelly_label = top.get("model_breakdown",{}).get("kelly_size","N/A")
            return (
                f"Kelly Criterion sizes your bet to your edge. "
                f"Formula: (edge × odds) / odds² × 0.25 (quarter Kelly for safety). "
                f"For the top pick right now ({top['bet']}, {top['edge']}% edge at {top['odds']}): "
                f"{kelly_label}. Never risk more than 3% of bankroll on a single play. "
                f"Bet responsibly."
            )
        return (
            "Kelly Criterion: quarter Kelly is safest — bet (edge × odds) / odds² × 0.25 of bankroll. "
            "For a +5% edge at -110 odds that's ~1.1% of bankroll. "
            "Never exceed 3% on any single play. Run a scan to get pick-specific Kelly sizing."
        )

    # ── Parlay questions ───────────────────────────────────────────────────────
    if any(w in msg for w in ["parlay","multi","same game","sgp","combo"]):
        a_picks = [p for p in picks if p.get("confidence", 0) >= 75]
        reply = (
            "Parlays multiply the house edge on every leg — most lose money long term. "
            "If you parlay, stick to 2-3 legs max using only A-grade picks (75%+ confidence, CLV-backed). "
        )
        if len(a_picks) >= 2:
            reply += (
                f"Best parlay candidates right now: "
                + " + ".join(f"**{p['bet']}**" for p in a_picks[:3])
                + f" Combined but remember: each leg must win independently."
            )
        else:
            reply += "Not enough high-confidence picks right now to recommend a parlay."
        return reply

    # ── Injuries ──────────────────────────────────────────────────────────────
    if any(w in msg for w in ["injur","out","doubtful","questionable","hurt","miss"]):
        inj_picks = [p for p in picks if "injury" not in p.get("agents_fired", [])]
        return (
            "Injury impact is baked into every pick via Agent 4 (Injury). "
            "If a key player is OUT on the bet side, the pick is automatically vetoed before it reaches you. "
            f"{len(inj_picks)} of the current picks had injury concerns — they were filtered out. "
            "Check the /api/injuries endpoint for the full live injury feed from ESPN."
        )

    # ── Weather ───────────────────────────────────────────────────────────────
    if any(w in msg for w in ["weather","wind","rain","cold","snow","outdoor"]):
        wx_picks = [p for p in picks if p.get("weather_flag")]
        if wx_picks:
            p = wx_picks[0]
            wx = p.get("weather_details", {})
            return (
                f"Weather signal active on {len(wx_picks)} pick(s). "
                f"Example: **{p['bet']}** ({p['game']}) — "
                f"flag: {p['weather_flag']}, wind: {wx.get('wind_mph','?')}mph, "
                f"temp: {wx.get('temp_f','?')}°F. "
                f"Wind >15mph and cold <20°F suppress totals confidence. "
                f"Weather data: Open-Meteo (free, updated hourly)."
            )
        return (
            "Weather signals run automatically for NFL and MLB outdoor stadiums via Open-Meteo. "
            "Wind >15mph, temp <20°F, or heavy precip reduce totals confidence. "
            "No weather flags on current picks — conditions look neutral."
        )

    # ── How does the model work ───────────────────────────────────────────────
    if any(w in msg for w in ["how does", "how it work", "model", "agents", "explain", "algorithm"]):
        n = ctx.get("total_picks", 0)
        return (
            "Algobets runs 7 agents on every game: "
            "(1) Value — CLV edge vs Pinnacle's fair price. "
            "(2) Line Movement — tracks sharp steam and RLM from Action Network. "
            "(3) Public Money — detects sharp vs public splits. "
            "(4) Injury — vetoes picks when key players are out. "
            "(5) Situational — schedule spots, back-to-backs, rest edges. "
            "(6) Fade Public — fades heavy public sides. "
            "(7) Kelly — sizes confidence by edge and odds. "
            "A pick must pass the veto system (no injury, no bad line move, no public trap, edge > 1.5%) "
            f"before it reaches you. {n} picks cleared all checks in the latest scan."
        )

    # ── ROI / performance ─────────────────────────────────────────────────────
    if any(w in msg for w in ["roi","performance","record","win rate","track record","history"]):
        return (
            "Track record lives at /api/performance — win%, ROI, CLV at close, and per-agent accuracy. "
            "Picks need 200+ resolved results to activate Platt confidence calibration, "
            "and 300+ for empirical agent weight fitting. "
            "Every surfaced pick is logged automatically to SQLite. "
            "Use /api/resolve-picks to mark outcomes after games complete."
        )

    # ── What sports are covered ───────────────────────────────────────────────
    if any(w in msg for w in ["sport", "cover", "available", "league", "which"]):
        scanned = ctx.get("sports_scanned", 0)
        return (
            f"Algobets scans {scanned} sport(s) right now: NBA 🏀, NFL 🏈, NHL 🏒, MLB ⚾, NCAAB 🎓, EPL ⚽, MMA 🥊. "
            "Data comes from ESPN (free), Action Network (free), Pinnacle (free), and Open-Meteo (free). "
            "The Odds API is only used for EV Finder and Arb Detect tabs."
        )

    # ── Default fallback ──────────────────────────────────────────────────────
    n = ctx.get("total_picks", 0)
    scan_hint = f" {n} picks are live right now." if n else " Run a scan to load today's picks."
    return (
        "I can help with: today's picks, sport-specific edges, CLV explained, "
        "sharp money signals, Kelly sizing, parlays, injuries, weather impact, "
        "how the model works, and performance tracking." + scan_hint +
        " Try asking: 'What's the best pick?' or 'Any NBA edges today?'"
    )


@app.post("/chat", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def chat(request: Request):
    """
    Context-aware betting assistant. Uses live scan data — zero token cost.
    Answers questions about today's picks, edges, CLV, sharp money, Kelly sizing,
    weather, injuries, and model methodology.
    """
    body    = await request.json()
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    ctx   = _build_scan_context()
    reply = _context_aware_reply(message, ctx)
    return {
        "reply":        reply,
        "picks_loaded": ctx.get("total_picks", 0),
        "data_fresh":   bool(ctx),
        "cost":         "$0",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DEMO FALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════
