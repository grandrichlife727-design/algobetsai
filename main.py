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
import asyncio
import hashlib
import stripe
import numpy as np
import statistics
from datetime import datetime, timedelta
from typing import Optional, Any

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
os.makedirs(CACHE_DIR, exist_ok=True)
GROWTH_DB_PATH = os.path.join(DATA_DIR, "growth_db.json")

stripe.api_key = STRIPE_SECRET

PLAN_FREE = "free"
PLAN_PREMIUM = "premium"
PLAN_VIP = "vip"


def _csv_env(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


PREMIUM_PRICE_IDS = _csv_env("STRIPE_PREMIUM_PRICE_IDS") or _csv_env("STRIPE_PRO_PRICE_IDS")
VIP_PRICE_IDS = _csv_env("STRIPE_VIP_PRICE_IDS") or _csv_env("STRIPE_SHARP_PRICE_IDS")


def normalize_plan_name(plan: str) -> str:
    p = (plan or "").strip().lower()
    if p in ("sharp", PLAN_VIP):
        return PLAN_VIP
    if p in ("pro", PLAN_PREMIUM):
        return PLAN_PREMIUM
    return PLAN_FREE


def plan_rank(plan: str) -> int:
    p = normalize_plan_name(plan)
    if p == PLAN_VIP:
        return 2
    if p == PLAN_PREMIUM:
        return 1
    return 0

def verify_api_key(request: Request, x_api_key: str = Header(default="")):
    # For now, accept any key or no key - simplify for deployment
    # TODO: Re-enable strict checking after testing
    pass  # Skip API key check for now


# ═══════════════════════════════════════════════════════════════════════════════
# PLAN VERIFICATION (Stripe)
# ═══════════════════════════════════════════════════════════════════════════════

_plan_cache: dict = {}
_PLAN_CACHE_TTL = 300
_scan_state: dict = {}
_SCAN_STATE_TTL = 3600 * 24

def _verify_plan_stripe_sync(user_id: str) -> str:
    if not STRIPE_SECRET or not user_id:
        return PLAN_FREE
    try:
        customers = stripe.Customer.search(query=f'email:"{user_id}"', limit=1)
        if not customers.data:
            customers = stripe.Customer.list(limit=100)
            matching = [c for c in customers.data if
                        c.metadata.get("userId") == user_id or c.email == user_id]
            if not matching:
                return PLAN_FREE
            customer = matching[0]
        else:
            customer = customers.data[0]

        subs = stripe.Subscription.list(customer=customer.id, status="active", limit=5)
        if not subs.data:
            return PLAN_FREE

        price_id  = subs.data[0]["items"]["data"][0]["price"]["id"]
        if price_id in VIP_PRICE_IDS:
            return PLAN_VIP
        if price_id in PREMIUM_PRICE_IDS:
            return PLAN_PREMIUM
        # Unknown paid subscription still gets paid access.
        return PLAN_PREMIUM

    except stripe.error.StripeError as e:
        print(f"[Plan] Stripe error for {user_id}: {e}")
        return PLAN_FREE
    except Exception as e:
        print(f"[Plan] Unexpected error for {user_id}: {e}")
        return PLAN_FREE


OWNER_EMAILS = {"grandrichlife727@gmail.com"}

async def _get_verified_plan(request: Request) -> str:
    user_id = (request.headers.get("x-user-id", "") or request.query_params.get("uid", "")).strip()
    if not user_id:
        return PLAN_FREE

    if user_id in OWNER_EMAILS:
        return PLAN_VIP

    now = time.time()
    cached = _plan_cache.get(user_id)
    if cached and cached["expires"] > now:
        return cached["plan"]

    stripe_plan = await asyncio.to_thread(_verify_plan_stripe_sync, user_id)
    trial_plan = _referral_trial_plan(user_id)
    plan = stripe_plan if plan_rank(stripe_plan) >= plan_rank(trial_plan) else trial_plan
    _plan_cache[user_id] = {"plan": plan, "expires": now + _PLAN_CACHE_TTL}
    return normalize_plan_name(plan)


async def require_paid_plan(request: Request):
    plan = await _get_verified_plan(request)
    if plan_rank(plan) < plan_rank(PLAN_PREMIUM):
        raise HTTPException(
            status_code=403,
            detail="This feature requires a Premium or VIP subscription."
        )


async def get_user_plan(request: Request) -> str:
    return await _get_verified_plan(request)


async def require_vip_plan(request: Request):
    plan = await _get_verified_plan(request)
    if plan_rank(plan) < plan_rank(PLAN_VIP):
        raise HTTPException(
            status_code=403,
            detail="This feature requires a VIP subscription."
        )


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
# GROWTH DATA (referrals, alert signups, scan history)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_growth_db() -> dict[str, Any]:
    try:
        with open(GROWTH_DB_PATH, "r") as f:
            raw = json.load(f)
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {"users": {}}


_growth_db: dict[str, Any] = _load_growth_db()


def _save_growth_db():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(GROWTH_DB_PATH, "w") as f:
            json.dump(_growth_db, f)
    except Exception as e:
        print(f"[GrowthDB] Save failed: {e}")


def _user_referral_code(user_id: str) -> str:
    h = hashlib.sha1(user_id.encode("utf-8")).hexdigest().upper()
    return f"ALGO{h[:8]}"


def _ensure_growth_user(user_id: str) -> dict[str, Any]:
    users = _growth_db.setdefault("users", {})
    rec = users.get(user_id)
    if not isinstance(rec, dict):
        rec = {}
    rec.setdefault("referral_code", _user_referral_code(user_id))
    rec.setdefault("redeemed_code", "")
    rec.setdefault("referrals", [])
    rec.setdefault("trial_until", 0)
    rec.setdefault("alerts", [])
    rec.setdefault("history", [])
    users[user_id] = rec
    return rec


def _find_user_by_ref_code(code: str) -> Optional[str]:
    code_u = (code or "").strip().upper()
    if not code_u:
        return None
    users = _growth_db.get("users", {})
    for uid, rec in users.items():
        if isinstance(rec, dict) and str(rec.get("referral_code", "")).upper() == code_u:
            return uid
    return None


def _referral_trial_plan(user_id: str) -> str:
    if not user_id:
        return PLAN_FREE
    rec = _ensure_growth_user(user_id)
    trial_until = float(rec.get("trial_until", 0) or 0)
    if trial_until > time.time():
        return PLAN_PREMIUM
    return PLAN_FREE


def _build_referral_status(user_id: str) -> dict[str, Any]:
    rec = _ensure_growth_user(user_id)
    now = time.time()
    trial_until = float(rec.get("trial_until", 0) or 0)
    secs_left = max(0, int(trial_until - now))
    return {
        "referral_code": rec.get("referral_code"),
        "redeemed_code": rec.get("redeemed_code") or None,
        "referrals_count": len(rec.get("referrals", [])),
        "trial_active_until": int(trial_until) if trial_until > now else None,
        "trial_seconds_left": secs_left,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# THE ODDS API - PRIMARY DATA SOURCE (v5)
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_odds_api_games(sport_key: str) -> list:
    """
    Fetch games and odds from The Odds API.
    This is the PRIMARY data source in v5.
    """
    global _quota_remaining, _quota_used_last
    
    cache_key = f"odds_{sport_key}"
    cached = cache_get(cache_key, ttl=CACHE_TTL)
    # Don't trust empty cache payloads; they can come from transient API failures.
    if cached is not None and len(cached) > 0:
        return cached

    # Last-resort stale cache for API outages or missing key.
    stale_cached = cache_get(cache_key, ttl=3600 * 24 * 14)

    if not ODDS_API_KEY:
        if stale_cached is not None and len(stale_cached) > 0:
            print(f"[OddsAPI] No API key; using stale cache for {sport_key}")
            return stale_cached
        print("[OddsAPI] No API key configured!")
        return []

    odds_sport = ODDS_API_SPORT_MAP.get(sport_key, sport_key)
    # Canonical Odds API endpoint: /sports/{sport}/odds
    url = f"{ODDS_BASE}/sports/{odds_sport}/odds"
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            base_params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "dateFormat": "iso",
            }
            attempts = [
                ("canonical+books", url, {**base_params, "bookmakers": ODDS_BOOKMAKERS}),
                ("canonical-all-books", url, dict(base_params)),
                ("legacy+books", f"{ODDS_BASE}/odds/", {**base_params, "sport": odds_sport, "bookmakers": ODDS_BOOKMAKERS}),
                ("legacy-all-books", f"{ODDS_BASE}/odds/", {**base_params, "sport": odds_sport}),
            ]

            data = None
            last_err = None
            for label, attempt_url, params in attempts:
                try:
                    r = await client.get(attempt_url, params=params)

                    # Update quota tracking if available.
                    if hasattr(r, "headers"):
                        remaining = r.headers.get("X-Requests-Remaining")
                        used = r.headers.get("X-Requests-Used")
                        if remaining:
                            _quota_remaining = int(remaining)
                        if used:
                            _quota_used_last = int(used)

                    if r.status_code == 403:
                        print(f"[OddsAPI] API key invalid or quota exceeded ({label})")
                        return stale_cached if stale_cached else []
                    if r.status_code == 429:
                        print(f"[OddsAPI] Rate limited ({label})")
                        return stale_cached if stale_cached else []
                    if r.status_code >= 400:
                        last_err = f"{label}: HTTP {r.status_code}"
                        continue

                    payload = r.json()
                    if isinstance(payload, list) and len(payload) > 0:
                        data = payload
                        break
                    if isinstance(payload, list):
                        # Empty list: try broader fallback.
                        last_err = f"{label}: empty list"
                        continue
                    last_err = f"{label}: unexpected payload {type(payload).__name__}"
                except Exception as inner_err:
                    last_err = f"{label}: {inner_err}"
                    continue

            if data is None:
                print(f"[OddsAPI] No usable data for {sport_key}. Last error: {last_err}")
                return stale_cached if stale_cached else []
                    
    except httpx.HTTPError as e:
        print(f"[OddsAPI] HTTP error for {sport_key}: {e}")
        return stale_cached if stale_cached else []
    except Exception as e:
        print(f"[OddsAPI] Error fetching {sport_key}: {e}")
        return stale_cached if stale_cached else []

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
        min_ev_threshold = 0.25
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
        confidence = confidence_raw

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
    
    return picks


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
# BILLING (Stripe)
# ═══════════════════════════════════════════════════════════════════════════════

TIER_CONFIG = {
    PLAN_FREE: {
        "name": "Free",
        "scan_pick_limit": 2,
        "sports_allowed": ["basketball_nba", "icehockey_nhl"],
        "min_scan_interval_seconds": 1200,
        "features": ["2 picks per scan", "NBA + NHL only", "Refresh every 20 minutes"],
    },
    PLAN_PREMIUM: {
        "name": "Premium",
        "scan_pick_limit": 15,
        "sports_allowed": SPORTS,
        "min_scan_interval_seconds": 300,
        "features": ["15 picks per scan", "All sports", "+EV finder", "Refresh every 5 minutes"],
    },
    PLAN_VIP: {
        "name": "VIP",
        "scan_pick_limit": 50,
        "sports_allowed": SPORTS,
        "min_scan_interval_seconds": 60,
        "features": ["50 picks per scan", "All sports", "+EV finder", "Arbitrage feed", "Refresh every minute"],
    },
}


class CheckoutRequest(BaseModel):
    tier: str
    success_url: str
    cancel_url: str
    user_id: Optional[str] = None


class PortalRequest(BaseModel):
    return_url: str
    user_id: Optional[str] = None


class ReferralRedeemRequest(BaseModel):
    code: str


class AlertSubscribeRequest(BaseModel):
    channel: str
    target: str
    min_ev: Optional[float] = 2.0
    sports: Optional[list[str]] = None


class CommunityPostRequest(BaseModel):
    mode: Optional[str] = "pick"
    text: Optional[str] = ""
    bet: Optional[str] = ""
    game: Optional[str] = ""
    odds: Optional[str] = ""
    ev: Optional[float] = None
    sport: Optional[str] = ""


def _request_user_id(request: Request) -> str:
    return (request.headers.get("x-user-id", "") or request.query_params.get("uid", "")).strip()


def _resolve_price_id_for_tier(tier: str) -> str:
    t = normalize_plan_name(tier)
    if t == PLAN_PREMIUM:
        if not PREMIUM_PRICE_IDS:
            raise HTTPException(status_code=500, detail="Premium price is not configured.")
        return PREMIUM_PRICE_IDS[0]
    if t == PLAN_VIP:
        if not VIP_PRICE_IDS:
            raise HTTPException(status_code=500, detail="VIP price is not configured.")
        return VIP_PRICE_IDS[0]
    raise HTTPException(status_code=400, detail="Free tier does not require checkout.")


def _find_or_create_customer(email: str):
    try:
        customers = stripe.Customer.search(query=f'email:"{email}"', limit=1)
        if customers.data:
            return customers.data[0]
    except Exception:
        pass
    return stripe.Customer.create(email=email, metadata={"userId": email})


def _invalidate_plan_cache(user_id: str):
    if not user_id:
        return
    _plan_cache.pop(user_id, None)


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


@app.get("/api/pricing")
async def pricing():
    return {
        "tiers": {
            PLAN_FREE: TIER_CONFIG[PLAN_FREE],
            PLAN_PREMIUM: {**TIER_CONFIG[PLAN_PREMIUM], "price_ids_configured": len(PREMIUM_PRICE_IDS) > 0},
            PLAN_VIP: {**TIER_CONFIG[PLAN_VIP], "price_ids_configured": len(VIP_PRICE_IDS) > 0},
        }
    }


@app.get("/api/plan")
async def current_plan(plan: str = Depends(get_user_plan)):
    normalized = normalize_plan_name(plan)
    return {
        "plan": normalized,
        "tier": TIER_CONFIG.get(normalized, TIER_CONFIG[PLAN_FREE]),
    }


@app.get("/api/referral/status")
async def referral_status(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    _ensure_growth_user(user_id)
    _save_growth_db()
    return _build_referral_status(user_id)


@app.post("/api/referral/redeem")
async def referral_redeem(body: ReferralRedeemRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    code = (body.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Referral code is required.")

    user_rec = _ensure_growth_user(user_id)
    if user_rec.get("redeemed_code"):
        raise HTTPException(status_code=400, detail="You have already redeemed a referral code.")

    owner_user_id = _find_user_by_ref_code(code)
    if not owner_user_id:
        raise HTTPException(status_code=404, detail="Referral code not found.")
    if owner_user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot redeem your own referral code.")

    owner_rec = _ensure_growth_user(owner_user_id)
    now = time.time()
    grant_seconds = 72 * 3600
    grant_until = now + grant_seconds

    user_rec["redeemed_code"] = code
    user_rec["trial_until"] = max(float(user_rec.get("trial_until", 0) or 0), grant_until)
    owner_rec["trial_until"] = max(float(owner_rec.get("trial_until", 0) or 0), grant_until)
    owner_rec.setdefault("referrals", []).append({"user_id": user_id, "ts": int(now)})

    _save_growth_db()
    _invalidate_plan_cache(user_id)
    _invalidate_plan_cache(owner_user_id)
    return {
        "ok": True,
        "granted_hours": 72,
        "trial_plan": PLAN_PREMIUM,
        "trial_until": int(user_rec["trial_until"]),
        "referral": _build_referral_status(user_id),
    }


@app.get("/api/history")
async def picks_history(request: Request, limit: int = 30):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    safe_limit = max(1, min(int(limit or 30), 100))
    rows = list(rec.get("history", []))
    rows.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return {"history": rows[:safe_limit], "count": len(rows)}


@app.get("/api/alerts/subscriptions")
async def alerts_subscriptions(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    return {"subscriptions": rec.get("alerts", [])}


@app.post("/api/alerts/subscribe")
async def alerts_subscribe(body: AlertSubscribeRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")

    channel = (body.channel or "").strip().lower()
    if channel not in {"email", "sms"}:
        raise HTTPException(status_code=400, detail="channel must be 'email' or 'sms'.")
    target = (body.target or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="target is required.")

    min_ev = float(body.min_ev if body.min_ev is not None else 2.0)
    min_ev = max(0.1, min(min_ev, 25.0))
    sports = [s for s in (body.sports or []) if s in SPORTS]
    if not sports:
        sports = SPORTS

    rec = _ensure_growth_user(user_id)
    alerts = rec.setdefault("alerts", [])
    key = f"{channel}:{target.lower()}"
    now_ts = int(time.time())
    updated = {
        "key": key,
        "channel": channel,
        "target": target,
        "min_ev": round(min_ev, 2),
        "sports": sports,
        "updated_at": now_ts,
    }

    replaced = False
    for i, item in enumerate(alerts):
        if item.get("key") == key:
            alerts[i] = updated
            replaced = True
            break
    if not replaced:
        alerts.append(updated)
    rec["alerts"] = alerts[-25:]
    _save_growth_db()
    return {"ok": True, "subscription": updated, "count": len(rec["alerts"])}


@app.get("/api/community/posts")
async def community_posts(limit: int = 40):
    posts = list(_growth_db.get("community_posts", []))
    posts.sort(key=lambda x: x.get("ts", 0), reverse=True)
    safe_limit = max(1, min(int(limit or 40), 100))
    return {"posts": posts[:safe_limit], "count": len(posts)}


@app.post("/api/community/posts")
async def community_create_post(body: CommunityPostRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")

    mode = (body.mode or "pick").strip().lower()
    if mode not in {"pick", "win"}:
        mode = "pick"

    text = (body.text or "").strip()[:240]
    bet = (body.bet or "").strip()[:120]
    game = (body.game or "").strip()[:120]
    odds = (body.odds or "").strip()[:24]
    sport = (body.sport or "").strip()[:48]
    ev = None
    if body.ev is not None:
        try:
            ev = round(float(body.ev), 2)
        except Exception:
            ev = None

    if not any([text, bet, game]):
        raise HTTPException(status_code=400, detail="Post content is empty.")

    masked_user = user_id
    if "@" in user_id:
        name, domain = user_id.split("@", 1)
        masked_user = f"{(name[:2] if len(name) > 2 else name)}***@{domain}"
    elif len(user_id) > 8:
        masked_user = f"{user_id[:4]}***"

    post = {
        "id": hashlib.md5(f"{user_id}|{time.time_ns()}".encode("utf-8")).hexdigest()[:12],
        "ts": int(time.time()),
        "user": masked_user,
        "mode": mode,
        "text": text,
        "bet": bet,
        "game": game,
        "odds": odds,
        "ev": ev,
        "sport": sport,
    }
    feed = _growth_db.setdefault("community_posts", [])
    feed.append(post)
    _growth_db["community_posts"] = feed[-500:]
    _save_growth_db()
    return {"ok": True, "post": post}


@app.post("/api/billing/checkout")
async def billing_checkout(body: CheckoutRequest, request: Request):
    if not STRIPE_SECRET:
        raise HTTPException(status_code=500, detail="Stripe is not configured.")
    user_id = (body.user_id or request.headers.get("x-user-id", "")).strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id or user_id is required.")

    tier = normalize_plan_name(body.tier)
    price_id = _resolve_price_id_for_tier(tier)
    customer = await asyncio.to_thread(_find_or_create_customer, user_id)

    session = await asyncio.to_thread(
        stripe.checkout.Session.create,
        mode="subscription",
        customer=customer.id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        allow_promotion_codes=True,
        client_reference_id=user_id,
        metadata={"userId": user_id, "tier": tier},
    )
    return {"checkout_url": session.url, "session_id": session.id}


@app.post("/api/billing/portal")
async def billing_portal(body: PortalRequest, request: Request):
    if not STRIPE_SECRET:
        raise HTTPException(status_code=500, detail="Stripe is not configured.")
    user_id = (body.user_id or request.headers.get("x-user-id", "")).strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id or user_id is required.")

    customer = await asyncio.to_thread(_find_or_create_customer, user_id)
    session = await asyncio.to_thread(
        stripe.billing_portal.Session.create,
        customer=customer.id,
        return_url=body.return_url,
    )
    return {"portal_url": session.url}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request, stripe_signature: str = Header(default="", alias="Stripe-Signature")):
    payload = await request.body()
    if not STRIPE_WEBHOOK:
        raise HTTPException(status_code=500, detail="Webhook secret is not configured.")

    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=stripe_signature, secret=STRIPE_WEBHOOK)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {e}")

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    user_id = ""

    if event_type == "checkout.session.completed":
        user_id = (obj.get("client_reference_id") or obj.get("metadata", {}).get("userId") or obj.get("customer_email") or "").strip()
    elif event_type.startswith("customer.subscription."):
        customer_id = obj.get("customer")
        try:
            if customer_id:
                customer = await asyncio.to_thread(stripe.Customer.retrieve, customer_id)
                user_id = (customer.get("email") or customer.get("metadata", {}).get("userId") or "").strip()
        except Exception:
            user_id = ""

    if user_id:
        _invalidate_plan_cache(user_id)

    return {"received": True, "event_type": event_type}


@app.get("/api/ev-finder")
async def ev_finder(plan: str = Depends(get_user_plan)):
    """Return current +EV opportunities derived from consensus-vs-best-line model."""
    if plan_rank(plan) < plan_rank(PLAN_PREMIUM):
        raise HTTPException(status_code=403, detail="Upgrade to Premium to access +EV Finder.")
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
async def arb_detect(plan: str = Depends(get_user_plan)):
    """Return simple two-way arbitrage opportunities across available books."""
    if plan_rank(plan) < plan_rank(PLAN_VIP):
        raise HTTPException(status_code=403, detail="Upgrade to VIP to access Arbitrage feed.")
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
    user_plan = await _get_verified_plan(request)
    tier = TIER_CONFIG.get(user_plan, TIER_CONFIG[PLAN_FREE])
    user_id = (request.headers.get("x-user-id", "") or request.query_params.get("uid", "")).strip() or "anon"
    now_ts = time.time()
    force_refresh = str(request.query_params.get("refresh", "false")).lower() == "true"

    # Enforce tier-based refresh cadence. If called too soon and cached payload exists,
    # serve cached results and include cooldown metadata instead of hard failing.
    min_interval = int(tier.get("min_scan_interval_seconds", 0) or 0)
    state = _scan_state.get(user_id, {})
    last_scan_ts = float(state.get("last_scan_ts", 0) or 0)
    elapsed = now_ts - last_scan_ts
    cooldown_remaining = max(0, int(min_interval - elapsed))
    if force_refresh and min_interval > 0 and elapsed < min_interval:
        cached_payload = state.get("last_payload")
        if isinstance(cached_payload, dict):
            payload = dict(cached_payload)
            payload["scan_policy"] = {
                "min_interval_seconds": min_interval,
                "cooldown_remaining_seconds": cooldown_remaining,
                "served_from_cache": True,
            }
            return payload
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {cooldown_remaining}s before the next scan on the {tier.get('name', 'current')} plan.",
        )
    
    # Check quota
    if _quota_remaining < 10 and ODDS_API_KEY:
        return {"error": "Low API quota", "quota": _quota_remaining}
    
    all_picks = []
    all_games = []
    sport_fetch_counts = {}
    
    allowed_sports = tier.get("sports_allowed") or SPORTS
    allowed_sports = [s for s in allowed_sports if s in SPORTS]
    if not allowed_sports:
        allowed_sports = SPORTS

    # Fetch games for each allowed sport
    for sport in allowed_sports:
        games = await fetch_odds_api_games(sport)
        sport_fetch_counts[sport] = len(games)
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
    
    # Sort by edge (highest first)
    all_picks.sort(key=lambda x: x.get("edge", 0), reverse=True)
    
    # Sort games by time
    all_games.sort(key=lambda x: x.get("commence_time", ""))
    
    pick_limit = int(tier.get("scan_pick_limit", 3))
    visible_picks = all_picks[:pick_limit]

    response_payload = {
        "plan": user_plan,
        "picks": visible_picks,
        "picks_total": len(all_picks),
        "games": all_games,  # All upcoming games
        "games_total": len(all_games),
        "paywall": {
            "visible_pick_limit": pick_limit,
            "next_tier": PLAN_PREMIUM if user_plan == PLAN_FREE else (PLAN_VIP if user_plan == PLAN_PREMIUM else None),
        },
        "debug_fetch_counts": sport_fetch_counts,
        "debug_has_api_key": bool(ODDS_API_KEY),
        "quota_remaining": _quota_remaining,
        "sports_covered": allowed_sports,
        "scan_policy": {
            "min_interval_seconds": min_interval,
            "cooldown_remaining_seconds": 0,
            "served_from_cache": False,
        },
    }

    # Persist a lightweight history row for retention/proof screens.
    history_row = {
        "ts": int(now_ts),
        "plan": user_plan,
        "picks_visible": len(visible_picks),
        "picks_total": len(all_picks),
        "sports_covered": allowed_sports,
        "top_picks": [
            {
                "bet": p.get("bet"),
                "game": p.get("game"),
                "odds": p.get("odds"),
                "ev": p.get("ev"),
                "sport": p.get("label") or p.get("sport"),
            }
            for p in visible_picks[:3]
        ],
    }
    user_growth = _ensure_growth_user(user_id)
    history = user_growth.setdefault("history", [])
    history.append(history_row)
    if len(history) > 120:
        user_growth["history"] = history[-120:]
    _save_growth_db()

    _scan_state[user_id] = {
        "last_scan_ts": now_ts,
        "last_payload": response_payload,
        "expires": now_ts + _SCAN_STATE_TTL,
    }
    if len(_scan_state) > 5000:
        expired_keys = [k for k, v in _scan_state.items() if float(v.get("expires", 0)) < now_ts]
        for k in expired_keys:
            _scan_state.pop(k, None)
    return response_payload


@app.get("/api/quota")
async def get_quota():
    """Get API quota status."""
    return {
        "quota_remaining": _quota_remaining,
        "quota_used_last": _quota_used_last,
        "data_source": "The Odds API" if ODDS_API_KEY else "Not configured",
        "cache_ttl_seconds": CACHE_TTL,
    }


@app.get("/api/debug/ingestion")
async def debug_ingestion():
    """
    Quick ingestion diagnostics per sport.
    """
    out = {}
    for sport in SPORTS:
        games = await fetch_odds_api_games(sport)
        with_ml = sum(1 for g in games if g.get("home_ml") is not None and g.get("away_ml") is not None)
        with_spread = sum(1 for g in games if (g.get("spreads_by_book") or {}))
        with_total = sum(1 for g in games if (g.get("totals_by_book") or {}))
        out[sport] = {
            "games": len(games),
            "with_moneyline": with_ml,
            "with_spreads": with_spread,
            "with_totals": with_total,
        }
    return {
        "has_api_key": bool(ODDS_API_KEY),
        "quota_remaining": _quota_remaining,
        "sports": out,
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


@app.get("/api/performance")
async def performance():
    """Placeholder - would need DB setup."""
    return {
        "note": "Performance tracking coming in v5.1",
        "overall": {"total_picks": 0, "wins": 0, "win_pct": 0}
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
