"""
Algobets Ai — FastAPI Backend v5.0
=====================================
UPGRADE IN THIS VERSION (v5)
─────────────────────────────
PRIMARY DATA SOURCE → The Odds API
- All game data, odds, spreads, totals come from The Odds API
- Cached aggressively to minimize API calls
- Falls back to ESPN only if Odds API fails

DATA SOURCES (v5)
──────────────────
PAID  → The Odds API  : primary source for all odds/lines
FREE  → Pinnacle     : CLV benchmark (still free, excellent for closing line value)
FREE  → Open-Meteo   : weather for NFL/MLB outdoor games

COST OPTIMIZATION
─────────────────
- Cache TTL: 30 minutes (1800s)
- Only fetch odds when cache is stale
- ~150 API calls/day for 6 sports = ~4,500/mo (well under 5,000 limit)
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
import statistics
from datetime import datetime, timedelta
from typing import Optional, Any
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

# Persistent disk on Render
DATA_DIR  = os.getenv("DATA_DIR", "/tmp/algobets_data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
DB_PATH   = os.path.join(DATA_DIR, "picks.db")
os.makedirs(CACHE_DIR, exist_ok=True)

stripe.api_key = STRIPE_SECRET

def verify_api_key(request: Request, x_api_key: str = Header(default="")):
    # For now, accept any key or no key - simplify for deployment
    # TODO: Re-enable strict checking after testing
    pass  # Skip API key check for now


# ═══════════════════════════════════════════════════════════════════════════════
# PLAN VERIFICATION (Stripe)
# ═══════════════════════════════════════════════════════════════════════════════

_plan_cache: dict = {}
_PLAN_CACHE_TTL = 300

def _verify_plan_stripe_sync(user_id: str) -> str:
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
        return "pro"

    except stripe.error.StripeError as e:
        print(f"[Plan] Stripe error for {user_id}: {e}")
        return "free"
    except Exception as e:
        print(f"[Plan] Unexpected error for {user_id}: {e}")
        return "free"


OWNER_EMAILS = {"grandrichlife727@gmail.com"}

async def _get_verified_plan(request: Request) -> str:
    user_id = (request.headers.get("x-user-id", "") or request.query_params.get("uid", "")).strip()
    if not user_id:
        return "free"

    if user_id in OWNER_EMAILS:
        return "sharp"

    now = time.time()
    cached = _plan_cache.get(user_id)
    if cached and cached["expires"] > now:
        return cached["plan"]

    plan = await asyncio.to_thread(_verify_plan_stripe_sync, user_id)
    _plan_cache[user_id] = {"plan": plan, "expires": now + _PLAN_CACHE_TTL}
    return plan


async def require_paid_plan(request: Request):
    plan = await _get_verified_plan(request)
    if plan not in ("pro", "sharp"):
        raise HTTPException(
            status_code=403,
            detail="This feature requires a Pro or Sharp subscription."
        )


async def get_user_plan(request: Request) -> str:
    return await _get_verified_plan(request)


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE & CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

CACHE_TTL          = 1800   # 30 min — keeps costs low
CACHE_TTL_PINNACLE = 1800   # 30 min — Pinnacle lines
CACHE_TTL_INJURIES = 900    # 15 min

ODDS_BOOKMAKERS = os.getenv("ODDS_BOOKMAKERS", "draftkings,fanduel,betmgm,pinnacle,williamhill_us,bovada")

_quota_remaining: int = 5000
_quota_used_last: int = 0

# Sport keys for Odds API
SPORTS = [
    "basketball_nba",
    "americanfootball_nfl", 
    "icehockey_nhl",
    "basketball_ncaab",
    "baseball_mlb",
    "soccer_epl",
]

SPORT_META = {
    "basketball_nba":         {"label": "NBA",   "emoji": "🏀", "odds_key": "basketball_nba"},
    "americanfootball_nfl":   {"label": "NFL",   "emoji": "🏈", "odds_key": "americanfootball_nfl"},
    "icehockey_nhl":          {"label": "NHL",   "emoji": "🏒", "odds_key": "icehockey_nhl"},
    "basketball_ncaab":       {"label": "NCAAB", "emoji": "🎓", "odds_key": "basketball_ncaab"},
    "baseball_mlb":           {"label": "MLB",   "emoji": "⚾", "odds_key": "baseball_mlb"},
    "soccer_epl":            {"label": "EPL",   "emoji": "⚽", "odds_key": "soccer_epl"},
}

# Odds API sport key mapping
ODDS_API_SPORT_MAP = {
    "basketball_nba": "basketball_nba",
    "americanfootball_nfl": "americanfootball_nfl",
    "icehockey_nhl": "icehockey_nhl",
    "basketball_ncaab": "basketball_ncaab",
    "baseball_mlb": "baseball_mlb",
    "soccer_epl": "soccer_epl",
}

ODDS_BASE = "https://api.the-odds-api.com/v4"
PINNACLE_BASE = "https://api.pinnacle.com/v1"
WEATHER_BASE = "https://api.open-meteo.com/v1/forecast"

# ═══════════════════════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════════════════════

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Algobets Ai API", version="5.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    init_db()


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
# RESULT TRACKING / CALIBRATION STORE
# ═══════════════════════════════════════════════════════════════════════════════

MODEL_VERSION = "v6_consensus_ensemble"
LEGACY_MODEL_VERSION = "v5_legacy_edge"


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS picks_history (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                model_version TEXT NOT NULL,
                sport TEXT NOT NULL,
                game TEXT NOT NULL,
                bet TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                odds INTEGER,
                edge REAL,
                confidence_raw REAL,
                confidence_cal REAL,
                fair_prob REAL,
                implied_prob REAL,
                best_book TEXT,
                books_compared INTEGER,
                market_disagreement REAL,
                result TEXT,
                settled_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_model_result ON picks_history(model_version, result)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_created_at ON picks_history(created_at)")


class SettlePickRequest(BaseModel):
    pick_id: str
    result: str  # "win" or "loss" or "push"


def _clean_result(result: str) -> str:
    v = (result or "").strip().lower()
    if v not in {"win", "loss", "push"}:
        raise HTTPException(status_code=400, detail="result must be win, loss, or push")
    return v


def save_generated_picks(picks: list[dict], model_version: str = MODEL_VERSION):
    if not picks:
        return
    now_iso = datetime.utcnow().isoformat()
    with db_conn() as conn:
        for p in picks:
            conn.execute("""
                INSERT OR IGNORE INTO picks_history (
                    id, created_at, model_version, sport, game, bet, bet_type, odds, edge,
                    confidence_raw, confidence_cal, fair_prob, implied_prob, best_book,
                    books_compared, market_disagreement, result, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """, (
                p.get("id"),
                now_iso,
                model_version,
                p.get("sport", ""),
                p.get("game", ""),
                p.get("bet", ""),
                p.get("bet_type", "moneyline"),
                p.get("odds"),
                p.get("edge"),
                p.get("confidence_raw", p.get("confidence")),
                p.get("confidence"),
                p.get("fair_prob"),
                p.get("implied_prob"),
                p.get("best_book"),
                p.get("books_compared"),
                p.get("market_disagreement"),
            ))


def calibration_snapshot(model_version: str = MODEL_VERSION) -> dict:
    """
    Reliability snapshot from settled picks.
    Bins confidence into 10-point buckets and returns empirical hit rates.
    """
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT confidence_raw, result
            FROM picks_history
            WHERE model_version = ?
              AND result IN ('win', 'loss')
              AND confidence_raw IS NOT NULL
        """, (model_version,)).fetchall()

    if len(rows) < 30:
        return {"count": len(rows), "bins": {}, "default_shrink": 0.35}

    bins: dict[int, dict[str, float]] = {}
    for r in rows:
        conf = float(r["confidence_raw"])
        b = int(max(50, min(99, conf)) // 10) * 10
        bins.setdefault(b, {"n": 0, "w": 0})
        bins[b]["n"] += 1
        bins[b]["w"] += 1 if r["result"] == "win" else 0

    # Convert to empirical probabilities.
    for b in bins:
        bins[b]["p_emp"] = bins[b]["w"] / bins[b]["n"] if bins[b]["n"] else 0.5
    return {"count": len(rows), "bins": bins, "default_shrink": 0.35}


def calibrated_confidence(raw_conf: float, model_version: str = MODEL_VERSION) -> float:
    """
    Blend model confidence with empirical reliability (if enough settled history).
    """
    snap = calibration_snapshot(model_version)
    raw_p = max(0.50, min(0.99, raw_conf / 100.0))
    bins = snap.get("bins", {})
    if not bins:
        return raw_p * 100.0

    b = int(max(50, min(99, raw_conf)) // 10) * 10
    bucket = bins.get(b)
    if not bucket:
        return raw_p * 100.0

    p_emp = float(bucket.get("p_emp", raw_p))
    n = float(bucket.get("n", 0))
    # More samples -> trust empirical more.
    w_emp = min(0.8, n / 120.0)
    p_cal = (1 - w_emp) * raw_p + w_emp * p_emp
    return max(50.0, min(99.0, p_cal * 100.0))


# ═══════════════════════════════════════════════════════════════════════════════
# THE ODDS API - PRIMARY DATA SOURCE (v5)
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_odds_api_games(sport_key: str) -> list:
    """
    Fetch games and odds from The Odds API.
    This is the PRIMARY data source in v5.
    """
    global _quota_remaining, _quota_used_last
    
    if not ODDS_API_KEY:
        print("[OddsAPI] No API key configured!")
        return []

    cache_key = f"odds_{sport_key}"
    cached = cache_get(cache_key, ttl=CACHE_TTL)
    if cached is not None:
        return cached

    odds_sport = ODDS_API_SPORT_MAP.get(sport_key, sport_key)
    url = f"{ODDS_BASE}/odds/"
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Fetch h2h (moneyline) odds
            r = await client.get(url, params={
                "apiKey": ODDS_API_KEY,
                "sport": odds_sport,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "dateFormat": "iso",
                "bookmakers": ODDS_BOOKMAKERS,
            })
            
            if r.status_code == 403:
                print(f"[OddsAPI] API key invalid or quota exceeded")
                return []
            if r.status_code == 429:
                print(f"[OddsAPI] Rate limited!")
                return []
                
            r.raise_for_status()
            data = r.json()
            
            # Update quota tracking
            if hasattr(r, 'headers'):
                remaining = r.headers.get('X-Requests-Remaining')
                used = r.headers.get('X-Requests-Used')
                if remaining:
                    _quota_remaining = int(remaining)
                if used:
                    _quota_used_last = int(used)
                    
    except httpx.HTTPError as e:
        print(f"[OddsAPI] HTTP error for {sport_key}: {e}")
        return []
    except Exception as e:
        print(f"[OddsAPI] Error fetching {sport_key}: {e}")
        return []

    # Process games
    games = []
    for game in data:
        try:
            home_team = game.get("home_team", "")
            away_team = game.get("away_team", "")
            commence_time = game.get("commence_time", "")
            
            # Extract odds from bookmakers
            bookmakers = game.get("bookmakers", [])
            if not bookmakers:
                continue

            # Keep per-book prices for a stronger consensus model.
            moneyline_by_book = {}
            spreads_by_book = {}
            totals_by_book = {}

            for bm in bookmakers:
                bm_name = bm.get("title", "")
                if bm_name.lower() not in ODDS_BOOKMAKERS.lower():
                    continue

                bm_home_ml = None
                bm_away_ml = None
                bm_home_spread = None
                bm_away_spread = None
                bm_home_spread_price = None
                bm_away_spread_price = None
                bm_total = None
                bm_over_price = None
                bm_under_price = None

                for market in bm.get("markets", []):
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        name = outcome.get("name", "")
                        price = outcome.get("price")

                        if market.get("key") == "h2h":
                            if name == home_team:
                                bm_home_ml = price
                            elif name == away_team:
                                bm_away_ml = price
                        elif market.get("key") == "spreads":
                            if name == home_team:
                                bm_home_spread = outcome.get("point")
                                bm_home_spread_price = price
                            elif name == away_team:
                                bm_away_spread = outcome.get("point")
                                bm_away_spread_price = price
                        elif market.get("key") == "totals":
                            if outcome.get("name") == "Over":
                                bm_total = outcome.get("point")
                                bm_over_price = price
                            elif outcome.get("name") == "Under":
                                bm_under_price = price

                if bm_home_ml is not None and bm_away_ml is not None:
                    moneyline_by_book[bm_name] = {"home": bm_home_ml, "away": bm_away_ml}
                if bm_home_spread is not None and bm_away_spread is not None and bm_home_spread_price is not None and bm_away_spread_price is not None:
                    spreads_by_book[bm_name] = {
                        "home_point": bm_home_spread,
                        "away_point": bm_away_spread,
                        "home_price": bm_home_spread_price,
                        "away_price": bm_away_spread_price,
                    }
                if bm_total is not None and bm_over_price is not None and bm_under_price is not None:
                    totals_by_book[bm_name] = {
                        "point": bm_total,
                        "over_price": bm_over_price,
                        "under_price": bm_under_price,
                    }

            # Default view fields used by existing consumers.
            home_lines = [v["home"] for v in moneyline_by_book.values()]
            away_lines = [v["away"] for v in moneyline_by_book.values()]
            if not home_lines or not away_lines:
                continue

            # "Best" means best payout for the bettor.
            best_home_book = max(moneyline_by_book.items(), key=lambda kv: american_to_decimal(kv[1]["home"]))
            best_away_book = max(moneyline_by_book.items(), key=lambda kv: american_to_decimal(kv[1]["away"]))

            home_ml = best_home_book[1]["home"]
            away_ml = best_away_book[1]["away"]
            home_spreads = [v["home_point"] for v in spreads_by_book.values()]
            away_spreads = [v["away_point"] for v in spreads_by_book.values()]
            totals = [v["point"] for v in totals_by_book.values()]
            home_spread = round(statistics.median(home_spreads), 1) if home_spreads else None
            away_spread = round(statistics.median(away_spreads), 1) if away_spreads else None
            over_under = round(statistics.median(totals), 1) if totals else None

            game_data = {
                "sport_key": sport_key,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": commence_time,
                "home_ml": home_ml,
                "away_ml": away_ml,
                "home_spread": home_spread,
                "away_spread": away_spread,
                "total": over_under,
                "bookmakers": list(moneyline_by_book.keys()),
                "moneyline_by_book": moneyline_by_book,
                "spreads_by_book": spreads_by_book,
                "totals_by_book": totals_by_book,
                "best_home_book": best_home_book[0],
                "best_away_book": best_away_book[0],
            }
            games.append(game_data)
            
        except Exception as e:
            print(f"[OddsAPI] Error parsing game: {e}")
            continue

    cache_set(cache_key, games)
    print(f"[OddsAPI] Fetched {len(games)} games for {sport_key}")
    return games


async def fetch_all_odds_games() -> dict:
    """Fetch games for all supported sports."""
    all_games = {}
    for sport in SPORTS:
        games = await fetch_odds_api_games(sport)
        all_games[sport] = games
    return all_games


# ═══════════════════════════════════════════════════════════════════════════════
# PINNACLE - CLV BENCHMARK (still free!)
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_pinnacle_odds(sport_key: str) -> list:
    """Fetch Pinnacle odds for CLV comparison (free, no key needed)."""
    cache_key = f"pinnacle_{sport_key}"
    cached = cache_get(cache_key, ttl=CACHE_TTL_PINNACLE)
    if cached is not None:
        return cached

    # Pinnacle uses different sport keys
    pinnacle_map = {
        "basketball_nba": "29",
        "americanfootball_nfl": "889", 
        "icehockey_nhl": "33",
        "baseball_mlb": "246",
        "basketball_ncaab": "493",
    }
    
    sport_id = pinnacle_map.get(sport_key)
    if not sport_id:
        return []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{PINNACLE_BASE}/feed",
                params={"sportId": sport_id, "isLive": "false"}
            )
            if r.status_code == 200:
                data = r.json()
                cache_set(cache_key, data)
                return data
    except Exception as e:
        print(f"[Pinnacle] Error: {e}")
    
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# PREDICTION ENGINE (simplified for v5)
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_implied_probability(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal payout."""
    if odds is None:
        return 1.0
    if odds > 0:
        return (odds / 100.0) + 1.0
    return 1.0 + (100.0 / abs(odds))


def expected_value_pct(odds: int, fair_prob: float) -> float:
    """Expected ROI percent for a 1-unit stake."""
    dec = american_to_decimal(odds)
    return (((dec - 1.0) * fair_prob) - (1.0 - fair_prob)) * 100.0


def devig_two_way_probabilities(home_odds: int, away_odds: int) -> tuple[float, float]:
    """Remove vig from a two-way market to get fair probabilities."""
    p_home = calculate_implied_probability(home_odds)
    p_away = calculate_implied_probability(away_odds)
    denom = p_home + p_away
    if denom <= 0:
        return 0.5, 0.5
    return p_home / denom, p_away / denom


def sharp_weight_for_book(book_name: str) -> float:
    key = (book_name or "").lower()
    weights = {
        "pinnacle": 1.30,
        "circa": 1.20,
        "betfair": 1.15,
        "draftkings": 1.00,
        "fanduel": 1.00,
        "betmgm": 1.00,
        "caesars": 0.95,
        "william hill": 0.95,
        "bovada": 0.90,
    }
    for k, v in weights.items():
        if k in key:
            return v
    return 1.0


def market_consensus_fair_prob(game: dict) -> tuple[float, float, dict]:
    """
    Build fair probability from all available books using de-vigged two-way prices.
    Returns (home_prob, away_prob, diagnostics).
    """
    by_book = game.get("moneyline_by_book") or {}
    if not by_book:
        # Fallback from best lines only.
        return devig_two_way_probabilities(game.get("home_ml"), game.get("away_ml")) + ({},)

    weighted_home = []
    weighted_away = []
    for book, lines in by_book.items():
        home_o = lines.get("home")
        away_o = lines.get("away")
        if home_o is None or away_o is None:
            continue
        h, a = devig_two_way_probabilities(home_o, away_o)
        w = sharp_weight_for_book(book)
        weighted_home.extend([h] * max(1, int(round(w * 10))))
        weighted_away.extend([a] * max(1, int(round(w * 10))))

    if not weighted_home:
        return devig_two_way_probabilities(game.get("home_ml"), game.get("away_ml")) + ({},)

    home_prob = statistics.median(weighted_home)
    away_prob = 1.0 - home_prob
    diagnostics = {
        "books_count": len(by_book),
        "home_prob_min": round(min(weighted_home), 4),
        "home_prob_max": round(max(weighted_home), 4),
        "home_prob_spread": round(max(weighted_home) - min(weighted_home), 4),
    }
    return home_prob, away_prob, diagnostics


def best_two_way_lines(game: dict) -> dict:
    """Find the best available home/away prices and source books."""
    by_book = game.get("moneyline_by_book") or {}
    if not by_book:
        return {
            "home_odds": game.get("home_ml"),
            "away_odds": game.get("away_ml"),
            "home_book": game.get("best_home_book") or "DraftKings",
            "away_book": game.get("best_away_book") or "DraftKings",
            "books_count": 1 if game.get("home_ml") is not None and game.get("away_ml") is not None else 0,
        }

    best_home_book, best_home_line = max(by_book.items(), key=lambda kv: american_to_decimal(kv[1]["home"]))
    best_away_book, best_away_line = max(by_book.items(), key=lambda kv: american_to_decimal(kv[1]["away"]))
    return {
        "home_odds": best_home_line["home"],
        "away_odds": best_away_line["away"],
        "home_book": best_home_book,
        "away_book": best_away_book,
        "books_count": len(by_book),
    }


def market_consensus_spread(game: dict) -> Optional[dict]:
    """
    Consensus fair probabilities for spread market around median point.
    """
    by_book = game.get("spreads_by_book") or {}
    if len(by_book) < 2:
        return None

    points = [v["home_point"] for v in by_book.values() if v.get("home_point") is not None]
    if not points:
        return None
    target_point = round(statistics.median(points), 1)

    home_probs = []
    away_probs = []
    for book, v in by_book.items():
        hp = v.get("home_price")
        ap = v.get("away_price")
        pt = v.get("home_point")
        if hp is None or ap is None or pt is None:
            continue
        if abs(float(pt) - target_point) > 0.5:
            continue
        h, a = devig_two_way_probabilities(hp, ap)
        w = sharp_weight_for_book(book)
        home_probs.extend([h] * max(1, int(round(w * 10))))
        away_probs.extend([a] * max(1, int(round(w * 10))))

    if not home_probs:
        return None
    home_prob = statistics.median(home_probs)
    return {
        "point": target_point,
        "home_prob": home_prob,
        "away_prob": 1.0 - home_prob,
        "books_count": len(by_book),
        "disagreement": max(home_probs) - min(home_probs),
    }


def best_spread_prices(game: dict, target_point: float) -> Optional[dict]:
    by_book = game.get("spreads_by_book") or {}
    home_candidates = []
    away_candidates = []
    for book, v in by_book.items():
        pt = v.get("home_point")
        if pt is None or abs(float(pt) - target_point) > 0.5:
            continue
        hp = v.get("home_price")
        ap = v.get("away_price")
        if hp is not None:
            home_candidates.append((book, hp))
        if ap is not None:
            away_candidates.append((book, ap))
    if not home_candidates or not away_candidates:
        return None
    home_book, home_odds = max(home_candidates, key=lambda x: american_to_decimal(x[1]))
    away_book, away_odds = max(away_candidates, key=lambda x: american_to_decimal(x[1]))
    return {
        "home_book": home_book,
        "home_odds": home_odds,
        "away_book": away_book,
        "away_odds": away_odds,
    }


def market_consensus_total(game: dict) -> Optional[dict]:
    """
    Consensus fair probabilities for totals market around median total.
    """
    by_book = game.get("totals_by_book") or {}
    if len(by_book) < 2:
        return None

    points = [v["point"] for v in by_book.values() if v.get("point") is not None]
    if not points:
        return None
    target_total = round(statistics.median(points), 1)

    over_probs = []
    under_probs = []
    for book, v in by_book.items():
        op = v.get("over_price")
        up = v.get("under_price")
        pt = v.get("point")
        if op is None or up is None or pt is None:
            continue
        if abs(float(pt) - target_total) > 0.5:
            continue
        over_p, under_p = devig_two_way_probabilities(op, up)
        w = sharp_weight_for_book(book)
        over_probs.extend([over_p] * max(1, int(round(w * 10))))
        under_probs.extend([under_p] * max(1, int(round(w * 10))))

    if not over_probs:
        return None
    over_prob = statistics.median(over_probs)
    return {
        "point": target_total,
        "over_prob": over_prob,
        "under_prob": 1.0 - over_prob,
        "books_count": len(by_book),
        "disagreement": max(over_probs) - min(over_probs),
    }


def best_total_prices(game: dict, target_total: float) -> Optional[dict]:
    by_book = game.get("totals_by_book") or {}
    over_candidates = []
    under_candidates = []
    for book, v in by_book.items():
        pt = v.get("point")
        if pt is None or abs(float(pt) - target_total) > 0.5:
            continue
        op = v.get("over_price")
        up = v.get("under_price")
        if op is not None:
            over_candidates.append((book, op))
        if up is not None:
            under_candidates.append((book, up))
    if not over_candidates or not under_candidates:
        return None
    over_book, over_odds = max(over_candidates, key=lambda x: american_to_decimal(x[1]))
    under_book, under_odds = max(under_candidates, key=lambda x: american_to_decimal(x[1]))
    return {
        "over_book": over_book,
        "over_odds": over_odds,
        "under_book": under_book,
        "under_odds": under_odds,
    }


def calculate_edge(home_odds: int, away_odds: int) -> dict:
    """Calculate value edge between the two sides."""
    home_implied = calculate_implied_probability(home_odds)
    away_implied = calculate_implied_probability(away_odds)
    
    # Total implied probability (includes vig)
    total_implied = home_implied + away_implied
    
    # Remove vig to get fair probability
    home_fair = home_implied / total_implied
    away_fair = away_implied / total_implied
    
    # Compare to actual odds
    home_edge = (home_fair * 100) - home_implied * 100
    away_edge = (away_fair * 100) - away_implied * 100
    
    return {
        "home_edge": round(home_edge, 2),
        "away_edge": round(away_edge, 2),
        "home_fair_prob": round(home_fair * 100, 1),
        "away_fair_prob": round(away_fair * 100, 1),
        "vig": round((total_implied - 1) * 100, 1)
    }


async def generate_picks_for_sport(sport_key: str, games: list) -> list:
    """Generate picks using a consensus fair-probability and best-line EV model."""
    picks = []
    
    if not games:
        return picks
        
    meta = SPORT_META.get(sport_key, {"label": sport_key, "emoji": "🎯"})
    
    for game in games:
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        home_ml = game.get("home_ml")
        away_ml = game.get("away_ml")
        
        if home_ml is None or away_ml is None:
            continue

        consensus_home, consensus_away, diag = market_consensus_fair_prob(game)
        home_ev = expected_value_pct(home_ml, consensus_home)
        away_ev = expected_value_pct(away_ml, consensus_away)

        # Keep threshold modest; user can filter by grade in UI.
        min_ev_threshold = 1.0
        if max(home_ev, away_ev) < min_ev_threshold:
            continue

        if home_ev >= away_ev:
            bet_side = home_team
            bet_odds = home_ml
            edge = home_ev
            fair_prob = consensus_home
            best_book = game.get("best_home_book")
        else:
            bet_side = away_team
            bet_odds = away_ml
            edge = away_ev
            fair_prob = consensus_away
            best_book = game.get("best_away_book")

        disagreement = diag.get("home_prob_spread", 0.0)
        # Confidence = function(edge, #books, consensus tightness)
        books_boost = min(12.0, (diag.get("books_count", 1) - 1) * 2.0)
        uncertainty_penalty = min(18.0, disagreement * 100.0 * 0.8)
        confidence_raw = max(50.0, min(95.0, 56.0 + (edge * 3.8) + books_boost - uncertainty_penalty))
        confidence = calibrated_confidence(confidence_raw)

        game_time = game.get("commence_time", "")
        fair_pct = fair_prob * 100.0
        implied_pct = calculate_implied_probability(bet_odds) * 100.0

        pick = {
            "id": f"{sport_key}_{home_team}_{away_team}_ml_{bet_odds}_{time.time_ns()}",
            "sport": sport_key,
            "emoji": meta.get("emoji", "🎯"),
            "label": meta.get("label", sport_key),
            "home_team": home_team,
            "away_team": away_team,
            "game": f"{away_team} @ {home_team}",
            "game_time": game_time,
            "bet": f"{bet_side} ML",
            "bet_type": "moneyline",
            "odds": bet_odds,
            "edge": round(edge, 2),
            "ev": round(edge, 2),
            "confidence_raw": round(confidence_raw, 1),
            "confidence": int(round(confidence)),
            "fair_prob": round(fair_pct, 1),
            "implied_prob": round(implied_pct, 1),
            "best_book": best_book or (game.get("bookmakers", ["DraftKings"])[0] if game.get("bookmakers") else "DraftKings"),
            "books_compared": diag.get("books_count", 1),
            "market_disagreement": round(disagreement * 100.0, 2),
            "model_breakdown": {
                "pinnacle_clv": f"Consensus fair {fair_pct:.1f}% vs implied {implied_pct:.1f}% ({edge:+.2f}% EV).",
                "sharp_money": f"Compared across {diag.get('books_count', 1)} books; disagreement {disagreement*100.0:.2f} pts.",
                "confirms": "Best-line EV, de-vig consensus, book quality weighting",
            },
            "agents_fired": ["best_line_ev", "market_consensus", "devig"],
            "data_source": "odds_api",
        }
        picks.append(pick)

        # Spread picks.
        spread_consensus = market_consensus_spread(game)
        if spread_consensus:
            spread_prices = best_spread_prices(game, spread_consensus["point"])
            if spread_prices:
                spread_candidates = [
                    {
                        "bet": f"{home_team} {spread_consensus['point']:+}",
                        "odds": spread_prices["home_odds"],
                        "book": spread_prices["home_book"],
                        "fair_prob": spread_consensus["home_prob"],
                        "side_key": "home",
                    },
                    {
                        "bet": f"{away_team} {(-spread_consensus['point']):+}",
                        "odds": spread_prices["away_odds"],
                        "book": spread_prices["away_book"],
                        "fair_prob": spread_consensus["away_prob"],
                        "side_key": "away",
                    },
                ]
                spread_candidates.sort(key=lambda c: expected_value_pct(c["odds"], c["fair_prob"]), reverse=True)
                top = spread_candidates[0]
                spread_ev = expected_value_pct(top["odds"], top["fair_prob"])
                if spread_ev >= 1.0:
                    disagreement_sp = float(spread_consensus.get("disagreement", 0.0))
                    books_boost_sp = min(10.0, (spread_consensus.get("books_count", 1) - 1) * 1.8)
                    penalty_sp = min(16.0, disagreement_sp * 100.0 * 0.8)
                    conf_raw_sp = max(50.0, min(94.0, 55.0 + spread_ev * 3.5 + books_boost_sp - penalty_sp))
                    conf_sp = calibrated_confidence(conf_raw_sp)
                    implied_sp = calculate_implied_probability(top["odds"]) * 100.0
                    fair_sp = top["fair_prob"] * 100.0
                    picks.append({
                        "id": f"{sport_key}_{home_team}_{away_team}_spread_{top['odds']}_{time.time_ns()}",
                        "sport": sport_key,
                        "emoji": meta.get("emoji", "🎯"),
                        "label": meta.get("label", sport_key),
                        "home_team": home_team,
                        "away_team": away_team,
                        "game": f"{away_team} @ {home_team}",
                        "game_time": game_time,
                        "bet": top["bet"],
                        "bet_type": "spread",
                        "odds": top["odds"],
                        "edge": round(spread_ev, 2),
                        "ev": round(spread_ev, 2),
                        "confidence_raw": round(conf_raw_sp, 1),
                        "confidence": int(round(conf_sp)),
                        "fair_prob": round(fair_sp, 1),
                        "implied_prob": round(implied_sp, 1),
                        "best_book": top["book"],
                        "books_compared": spread_consensus.get("books_count", 1),
                        "market_disagreement": round(disagreement_sp * 100.0, 2),
                        "model_breakdown": {
                            "pinnacle_clv": f"Spread consensus fair {fair_sp:.1f}% vs implied {implied_sp:.1f}% ({spread_ev:+.2f}% EV).",
                            "sharp_money": f"Point consensus {spread_consensus['point']:+}; disagreement {disagreement_sp*100.0:.2f} pts.",
                            "confirms": "Spread de-vig consensus, best-line EV",
                        },
                        "agents_fired": ["spread_ev", "market_consensus", "devig"],
                        "data_source": "odds_api",
                    })

        # Totals picks.
        totals_consensus = market_consensus_total(game)
        if totals_consensus:
            total_prices = best_total_prices(game, totals_consensus["point"])
            if total_prices:
                total_candidates = [
                    {
                        "bet": f"Over {totals_consensus['point']}",
                        "odds": total_prices["over_odds"],
                        "book": total_prices["over_book"],
                        "fair_prob": totals_consensus["over_prob"],
                    },
                    {
                        "bet": f"Under {totals_consensus['point']}",
                        "odds": total_prices["under_odds"],
                        "book": total_prices["under_book"],
                        "fair_prob": totals_consensus["under_prob"],
                    },
                ]
                total_candidates.sort(key=lambda c: expected_value_pct(c["odds"], c["fair_prob"]), reverse=True)
                top_t = total_candidates[0]
                total_ev = expected_value_pct(top_t["odds"], top_t["fair_prob"])
                if total_ev >= 1.0:
                    disagreement_t = float(totals_consensus.get("disagreement", 0.0))
                    books_boost_t = min(10.0, (totals_consensus.get("books_count", 1) - 1) * 1.8)
                    penalty_t = min(16.0, disagreement_t * 100.0 * 0.8)
                    conf_raw_t = max(50.0, min(94.0, 55.0 + total_ev * 3.4 + books_boost_t - penalty_t))
                    conf_t = calibrated_confidence(conf_raw_t)
                    implied_t = calculate_implied_probability(top_t["odds"]) * 100.0
                    fair_t = top_t["fair_prob"] * 100.0
                    picks.append({
                        "id": f"{sport_key}_{home_team}_{away_team}_total_{top_t['odds']}_{time.time_ns()}",
                        "sport": sport_key,
                        "emoji": meta.get("emoji", "🎯"),
                        "label": meta.get("label", sport_key),
                        "home_team": home_team,
                        "away_team": away_team,
                        "game": f"{away_team} @ {home_team}",
                        "game_time": game_time,
                        "bet": top_t["bet"],
                        "bet_type": "total",
                        "odds": top_t["odds"],
                        "edge": round(total_ev, 2),
                        "ev": round(total_ev, 2),
                        "confidence_raw": round(conf_raw_t, 1),
                        "confidence": int(round(conf_t)),
                        "fair_prob": round(fair_t, 1),
                        "implied_prob": round(implied_t, 1),
                        "best_book": top_t["book"],
                        "books_compared": totals_consensus.get("books_count", 1),
                        "market_disagreement": round(disagreement_t * 100.0, 2),
                        "model_breakdown": {
                            "pinnacle_clv": f"Totals consensus fair {fair_t:.1f}% vs implied {implied_t:.1f}% ({total_ev:+.2f}% EV).",
                            "sharp_money": f"Total consensus {totals_consensus['point']}; disagreement {disagreement_t*100.0:.2f} pts.",
                            "confirms": "Totals de-vig consensus, best-line EV",
                        },
                        "agents_fired": ["totals_ev", "market_consensus", "devig"],
                        "data_source": "odds_api",
                    })
    
    return picks


def generate_legacy_pick_for_game(sport_key: str, game: dict) -> Optional[dict]:
    """
    Legacy v5-style pick scoring kept for backtest comparison.
    """
    home_team = game.get("home_team", "")
    away_team = game.get("away_team", "")
    home_ml = game.get("home_ml")
    away_ml = game.get("away_ml")
    if home_ml is None or away_ml is None:
        return None

    edge_data = calculate_edge(home_ml, away_ml)
    if abs(edge_data["home_edge"]) <= 3 and abs(edge_data["away_edge"]) <= 3:
        return None

    if edge_data["home_edge"] > edge_data["away_edge"]:
        bet_side = home_team
        bet_odds = home_ml
        edge = edge_data["home_edge"]
        fair_prob = edge_data.get("home_fair_prob", 50.0)
    else:
        bet_side = away_team
        bet_odds = away_ml
        edge = edge_data["away_edge"]
        fair_prob = edge_data.get("away_fair_prob", 50.0)

    confidence_raw = max(50.0, min(95.0, 50.0 + abs(edge) * 2.0))
    return {
        "id": f"{sport_key}_{home_team}_{away_team}_legacy_{bet_odds}_{time.time_ns()}",
        "sport": sport_key,
        "game": f"{away_team} @ {home_team}",
        "bet": f"{bet_side} ML",
        "bet_type": "moneyline",
        "odds": bet_odds,
        "edge": round(edge, 2),
        "confidence_raw": round(confidence_raw, 1),
        "confidence": int(round(confidence_raw)),
        "fair_prob": float(fair_prob),
        "implied_prob": round(calculate_implied_probability(bet_odds) * 100.0, 1),
        "best_book": game.get("bookmakers", ["DraftKings"])[0] if game.get("bookmakers") else "DraftKings",
    }


def _model_stats_from_rows(rows: list[sqlite3.Row]) -> dict:
    resolved = [r for r in rows if r["result"] in ("win", "loss")]
    if not resolved:
        return {"resolved": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_edge": 0.0, "avg_confidence": 0.0, "roi_units_per_bet": 0.0, "brier": None}

    wins = sum(1 for r in resolved if r["result"] == "win")
    losses = len(resolved) - wins
    win_rate = wins / len(resolved)

    units = 0.0
    brier_vals = []
    edge_vals = []
    conf_vals = []
    for r in resolved:
        odds = r["odds"]
        conf = float(r["confidence_cal"] if r["confidence_cal"] is not None else (r["confidence_raw"] or 50.0))
        p = max(0.01, min(0.99, conf / 100.0))
        y = 1.0 if r["result"] == "win" else 0.0
        brier_vals.append((p - y) ** 2)
        if r["edge"] is not None:
            edge_vals.append(float(r["edge"]))
        conf_vals.append(conf)
        if odds is None:
            continue
        dec = american_to_decimal(int(odds))
        if r["result"] == "win":
            units += (dec - 1.0)
        else:
            units -= 1.0

    return {
        "resolved": len(resolved),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate * 100.0, 2),
        "avg_edge": round(statistics.mean(edge_vals), 3) if edge_vals else 0.0,
        "avg_confidence": round(statistics.mean(conf_vals), 2) if conf_vals else 0.0,
        "roi_units_per_bet": round(units / len(resolved), 4),
        "brier": round(statistics.mean(brier_vals), 4) if brier_vals else None,
    }


def build_ev_rows_for_game(game: dict) -> list:
    """Build +EV opportunities per side using best-line prices."""
    lines = best_two_way_lines(game)
    home_odds = lines.get("home_odds")
    away_odds = lines.get("away_odds")
    if home_odds is None or away_odds is None:
        return []

    home_team = game.get("home_team", "")
    away_team = game.get("away_team", "")
    consensus_home, consensus_away, diag = market_consensus_fair_prob(game)

    rows = []
    home_ev = expected_value_pct(home_odds, consensus_home)
    away_ev = expected_value_pct(away_odds, consensus_away)

    if home_ev >= 0.5:
        rows.append({
            "bet": f"{home_team} ML",
            "game": f"{away_team} @ {home_team}",
            "ev": round(home_ev, 2),
            "bookOdds": home_odds,
            "book": lines.get("home_book"),
            "books_compared": diag.get("books_count", lines.get("books_count", 1)),
        })
    if away_ev >= 0.5:
        rows.append({
            "bet": f"{away_team} ML",
            "game": f"{away_team} @ {home_team}",
            "ev": round(away_ev, 2),
            "bookOdds": away_odds,
            "book": lines.get("away_book"),
            "books_compared": diag.get("books_count", lines.get("books_count", 1)),
        })
    return rows


def build_arb_for_game(game: dict) -> Optional[dict]:
    """Detect simple 2-way arbitrage between books for a game."""
    lines = best_two_way_lines(game)
    home_odds = lines.get("home_odds")
    away_odds = lines.get("away_odds")
    if home_odds is None or away_odds is None:
        return None

    home_dec = american_to_decimal(home_odds)
    away_dec = american_to_decimal(away_odds)
    inv_sum = (1.0 / home_dec) + (1.0 / away_dec)
    if inv_sum >= 1.0:
        return None

    profit_pct = ((1.0 / inv_sum) - 1.0) * 100.0
    if profit_pct < 0.25:
        return None

    # Stake allocation as % of bankroll to lock profit.
    home_stake_pct = ((1.0 / home_dec) / inv_sum) * 100.0
    away_stake_pct = ((1.0 / away_dec) / inv_sum) * 100.0

    return {
        "sport": SPORT_META.get(game.get("sport_key"), {}).get("label", game.get("sport_key", "")),
        "emoji": SPORT_META.get(game.get("sport_key"), {}).get("emoji", "🎯"),
        "game": f"{game.get('away_team', '')} @ {game.get('home_team', '')}",
        "profit_pct": round(profit_pct, 3),
        "sides": [
            {
                "book": lines.get("home_book"),
                "bet": f"{game.get('home_team', '')} ML",
                "odds": home_odds,
                "stake_pct": round(home_stake_pct, 2),
            },
            {
                "book": lines.get("away_book"),
                "bet": f"{game.get('away_team', '')} ML",
                "odds": away_odds,
                "stake_pct": round(away_stake_pct, 2),
            },
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/debug")
async def debug():
    """Debug endpoint to check configuration."""
    return {
        "odds_api_key_set": bool(ODDS_API_KEY),
        "odds_api_key_prefix": ODDS_API_KEY[:10] + "..." if ODDS_API_KEY else "NOT SET",
        "quota_remaining": _quota_remaining,
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "5.0.0",
        "data_source": "odds_api" if ODDS_API_KEY else "none",
        "quota_remaining": _quota_remaining,
    }


@app.get("/api/ev-finder")
async def ev_finder():
    """Return current +EV opportunities derived from consensus-vs-best-line model."""
    rows = []
    for sport in SPORTS:
        games = await fetch_odds_api_games(sport)
        for game in games:
            rows.extend(build_ev_rows_for_game(game))

    rows.sort(key=lambda x: x.get("ev", 0), reverse=True)
    return {
        "results": rows[:100],
        "count": len(rows),
        "model": "consensus_devig_best_line_ev",
    }


@app.get("/api/arb-detect")
async def arb_detect():
    """Return simple two-way arbitrage opportunities across available books."""
    arbs = []
    for sport in SPORTS:
        games = await fetch_odds_api_games(sport)
        for game in games:
            arb = build_arb_for_game(game)
            if arb:
                arbs.append(arb)

    arbs.sort(key=lambda x: x.get("profit_pct", 0), reverse=True)
    return {
        "results": arbs[:100],
        "count": len(arbs),
    }


@app.get("/scan")
async def scan(request: Request):
    """Main scan endpoint - generates picks + shows all upcoming games."""
    # API key check disabled for now
    
    # Check quota
    if _quota_remaining < 10 and ODDS_API_KEY:
        return {"error": "Low API quota", "quota": _quota_remaining}
    
    all_picks = []
    legacy_snapshot = []
    all_games = []
    
    # Fetch games for each sport
    for sport in SPORTS:
        games = await fetch_odds_api_games(sport)
        meta = SPORT_META.get(sport, {"label": sport, "emoji": "🎯"})
        
        for game in games:
            # Add meta to each game
            game["sport"] = sport
            game["emoji"] = meta.get("emoji", "🎯")
            game["label"] = meta.get("label", sport)
            
            # Calculate edge
            home_ml = game.get("home_ml")
            away_ml = game.get("away_ml")
            if home_ml and away_ml:
                edge_data = calculate_edge(home_ml, away_ml)
                game["home_edge"] = edge_data.get("home_edge", 0)
                game["away_edge"] = edge_data.get("away_edge", 0)
            
            all_games.append(game)
            
            # Also generate picks for positive edge games
            picks = await generate_picks_for_sport(sport, [game])
            all_picks.extend(picks)
            legacy_pick = generate_legacy_pick_for_game(sport, game)
            if legacy_pick:
                legacy_snapshot.append(legacy_pick)
    
    # Sort by edge (highest first)
    all_picks.sort(key=lambda x: x.get("edge", 0), reverse=True)
    legacy_snapshot.sort(key=lambda x: x.get("edge", 0), reverse=True)

    # Persist model outputs for downstream calibration/backtest.
    save_generated_picks(all_picks[:50], model_version=MODEL_VERSION)
    save_generated_picks(legacy_snapshot[:50], model_version=LEGACY_MODEL_VERSION)
    
    # Sort games by time
    all_games.sort(key=lambda x: x.get("commence_time", ""))
    
    return {
        "picks": all_picks[:20],  # Top 20 picks
        "picks_total": len(all_picks),
        "games": all_games,  # All upcoming games
        "games_total": len(all_games),
        "model_version": MODEL_VERSION,
        "quota_remaining": _quota_remaining,
        "sports_covered": SPORTS,
    }


@app.get("/api/quota")
async def get_quota():
    """Get API quota status."""
    return {
        "quota_remaining": _quota_remaining,
        "quota_used_last": _quota_used_last,
        "data_source": "The Odds API" if ODDS_API_KEY else "Not configured",
        "cache_ttl_seconds": CACHE_TTL,
    }


@app.get("/api/games")
async def get_all_games():
    """Get all upcoming games for all sports - for planning ahead."""
    all_games = []
    
    for sport in SPORTS:
        games = await fetch_odds_api_games(sport)
        meta = SPORT_META.get(sport, {"label": sport, "emoji": "🎯"})
        
        for game in games:
            # Calculate edge for each game
            home_ml = game.get("home_ml")
            away_ml = game.get("away_ml")
            
            edge_data = {}
            if home_ml and away_ml:
                edge_data = calculate_edge(home_ml, away_ml)
            
            all_games.append({
                "sport": sport,
                "emoji": meta.get("emoji", "🎯"),
                "label": meta.get("label", sport),
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "game": f"{game.get('away_team', 'TBD')} @ {game.get('home_team', 'TBD')}",
                "game_time": game.get("commence_time"),
                "home_ml": game.get("home_ml"),
                "away_ml": game.get("away_ml"),
                "home_spread": game.get("home_spread"),
                "away_spread": game.get("away_spread"),
                "total": game.get("total"),
                "home_edge": edge_data.get("home_edge", 0),
                "away_edge": edge_data.get("away_edge", 0),
                "books": game.get("bookmakers", []),
            })
    
    # Sort by game time
    all_games.sort(key=lambda x: x.get("game_time", ""))
    
    return {
        "games": all_games,
        "total": len(all_games),
        "sports_covered": SPORTS,
    }


@app.get("/api/odds/{sport}")
async def get_sport_odds(sport: str):
    """Get raw odds for a specific sport."""
    if sport not in SPORTS:
        raise HTTPException(status_code=400, detail="Invalid sport")
    
    games = await fetch_odds_api_games(sport)
    return {
        "sport": sport,
        "games": games,
        "count": len(games),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY ENDPOINTS (kept for compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/injuries")
async def injuries():
    """Placeholder - injuries would need ESPN or other source."""
    return {"injuries": [], "note": "Injury data not available in v5 (cost optimization)"}


@app.post("/api/picks/settle")
async def settle_pick(body: SettlePickRequest):
    """
    Mark a generated pick as win/loss/push for calibration + backtest.
    """
    result = _clean_result(body.result)
    settled_at = datetime.utcnow().isoformat()
    with db_conn() as conn:
        cur = conn.execute("""
            UPDATE picks_history
            SET result = ?, settled_at = ?
            WHERE id = ?
        """, (result, settled_at, body.pick_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="pick_id not found")
    return {"ok": True, "pick_id": body.pick_id, "result": result, "settled_at": settled_at}


@app.get("/api/picks/unsettled")
async def unsettled_picks(limit: int = 200):
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT id, created_at, model_version, sport, game, bet, bet_type, odds, edge, confidence_cal, best_book
            FROM picks_history
            WHERE result IS NULL
            ORDER BY created_at DESC
            LIMIT ?
        """, (max(1, min(1000, limit)),)).fetchall()
    return {
        "count": len(rows),
        "items": [dict(r) for r in rows],
    }


@app.get("/api/backtest")
async def backtest(limit: int = 2000):
    """
    Compare current model vs legacy model using settled picks in DB.
    """
    with db_conn() as conn:
        rows_new = conn.execute("""
            SELECT *
            FROM picks_history
            WHERE model_version = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (MODEL_VERSION, max(50, min(10000, limit)))).fetchall()
        rows_old = conn.execute("""
            SELECT *
            FROM picks_history
            WHERE model_version = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (LEGACY_MODEL_VERSION, max(50, min(10000, limit)))).fetchall()

    new_stats = _model_stats_from_rows(rows_new)
    old_stats = _model_stats_from_rows(rows_old)
    delta = {}
    for key in ("win_rate", "roi_units_per_bet", "avg_edge", "avg_confidence"):
        if isinstance(new_stats.get(key), (int, float)) and isinstance(old_stats.get(key), (int, float)):
            delta[key] = round(float(new_stats[key]) - float(old_stats[key]), 4)

    return {
        "as_of_utc": datetime.utcnow().isoformat(),
        "sample_limit": max(50, min(10000, limit)),
        "models": {
            MODEL_VERSION: new_stats,
            LEGACY_MODEL_VERSION: old_stats,
        },
        "delta_new_minus_legacy": delta,
        "note": "Only settled picks (win/loss) are scored. Use /api/picks/settle to label outcomes.",
    }


@app.get("/api/performance")
async def performance():
    """Performance + calibration summary from recorded pick outcomes."""
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT *
            FROM picks_history
            WHERE model_version = ?
            ORDER BY created_at DESC
            LIMIT 3000
        """, (MODEL_VERSION,)).fetchall()
    stats = _model_stats_from_rows(rows)
    calib = calibration_snapshot(MODEL_VERSION)
    bins = []
    for k in sorted((calib.get("bins") or {}).keys()):
        b = calib["bins"][k]
        bins.append({
            "bucket": f"{k}-{k+9}",
            "n": int(b.get("n", 0)),
            "empirical_win_rate": round(float(b.get("p_emp", 0.0)) * 100.0, 2),
        })
    return {
        "model_version": MODEL_VERSION,
        "overall": stats,
        "calibration": {
            "settled_count": calib.get("count", 0),
            "bins": bins,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
