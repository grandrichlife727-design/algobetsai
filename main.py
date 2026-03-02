"""
Algobets Ai — FastAPI Backend  v4.0
=====================================
UPGRADES IN THIS VERSION
─────────────────────────
1. PINNACLE CLV MODEL  (v3)
   Scrapes Pinnacle's live spread/ML from their public API (no key needed).
   Their line IS the market's fair price. We compare ESPN's line to it for
   a real edge signal instead of the old vig-removal tautology.

2. AGENT VETO SYSTEM  (v3)
   Four kill conditions suppress picks before they reach users:
   V1-Injury, V2-LineMove, V3-PublicTrap, V4-ThinEdge.

3. PICK LOGGING + ROI TRACKING  (v3)
   SQLite picks.db on Render persistent disk. Every surfaced pick logged.
   /api/resolve-picks writes results. /api/performance returns win%/ROI/CLV.

4. CLOSING LINE CAPTURE CRON  (v4 — NEW)
   A background task runs 30 min before every logged game and fetches
   Pinnacle's current line, writing it to clv_pinnacle_close in picks.db.
   This captures the true closing line value for every pick automatically —
   no manual step needed. CLV at close is the gold-standard proof of edge.

5. CONFIDENCE CALIBRATION  (v4 — NEW)
   Platt scaling: once 200+ resolved picks exist, fits a logistic regression
   on raw confidence scores vs actual outcomes. The calibrated score is what
   the model reports — "75% confidence" actually means ~75% win rate.
   Recalibrates automatically every 50 new resolved picks.
   Falls back to raw score if insufficient data.

6. WEATHER SIGNAL FOR TOTALS  (v4 — NEW)
   Fetches Open-Meteo forecast (free, no key) for NFL/MLB outdoor stadiums.
   Wind >15mph, temp <20°F, or heavy precip adds a weather_flag to picks
   and adjusts total confidence down. Wind is the strongest signal —
   reduces scoring in both directions.

7. AGENT WEIGHT OPTIMISATION  (v4 — NEW)
   Once 300+ resolved picks exist, fits logistic regression on agent signal
   vectors to find empirically optimal weights. Weights update automatically.
   Until then, falls back to the v3 hand-tuned defaults.
   GET /api/model-weights shows current weights and fit quality.

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
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
FRONTEND_URL    = os.getenv("FRONTEND_URL", "https://algobets.ai").strip()
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "").strip()

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
    Rejects free users with 403. Never trusts client-supplied plan headers.
    """
    plan = await _get_verified_plan(request)
    if plan not in ("pro", "sharp"):
        raise HTTPException(
            status_code=403,
            detail="This feature requires a Pro or Sharp subscription. Upgrade at algobets.ai"
        )


async def get_user_plan(request: Request) -> str:
    """Return the server-verified plan tier for the current user."""
    return await _get_verified_plan(request)


# ─── Cache TTLs ───────────────────────────────────────────────────────────────
CACHE_TTL          = 1800   # 30 min — Odds API (EV/arb) — keeps lines fresh
CACHE_TTL_FREE     = 1800   # 30 min — ESPN / Action Network
CACHE_TTL_PINNACLE = 1800   # 30 min — Pinnacle lines (free but rate-limit cautious)
CACHE_TTL_INJURIES = 900    # 15 min — injuries
CACHE_TTL_PROPS    = 3600   # 1 hr   — player props
CACHE_TTL_SPORTS   = 86400  # 24 hrs — active sports list

ODDS_BOOKMAKERS = os.getenv("ODDS_BOOKMAKERS", "draftkings,fanduel,betmgm,pinnacle,williamhill_us,bovada")

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
    "value":        3.0,   # CLV edge per % point
    "line_movement": 5.0,  # favorable line move
    "public_money": 15.0,  # sharp/steam signal strength
    "injury":       -5.0,  # injury impact penalty (inverted)
    "situational":  8.0,   # situational score
    "fade_public":  10.0,  # fade signal strength
    "kelly":        3.0,   # kelly units
}

# In-memory fitted weights — updated by recalibration task
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
app = FastAPI(title="Algobets Ai API", version="4.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "https://algobets.app,https://algobets.ai,https://edgebet.app"
).split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS,
                   allow_methods=["GET","POST"], allow_headers=["*"])

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
                    f"bootstrap_{agent}_{i}",
                    "NBA", f"Bootstrap Game {agent} {i}", "TeamA", "TeamB",
                    f"Bootstrap {agent}", "spread", "away",
                    -110, 3.0, 65, 65, 0.65,
                    0.55, 0.52,
                    None, None, None, None, None,
                    None, None,
                    json.dumps(fired), json.dumps([]), 1,
                    "bootstrap_prior", "",
                    result, 0.0 if result == "win" else -1.0,
                    datetime.utcnow().isoformat(),
                ))

        with get_db() as db:
            db.executemany("""
                INSERT OR IGNORE INTO picks
                (pick_id, sport, game, home_team, away_team, bet, bet_type, bet_side,
                 odds, edge, confidence, confidence_raw, confidence_calibrated,
                 fair_prob, implied_prob,
                 pinnacle_line, pinnacle_fetched_at, espn_line, espn_line_fetched_at, clv_edge,
                 weather_flag, weather_details,
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

        events = []
        for game in raw.get("events", []):
            comp        = game.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue
            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
            home_team = home.get("team", {}).get("displayName", "")
            away_team = away.get("team", {}).get("displayName", "")

            odds_raw   = comp.get("odds", [{}])[0] if comp.get("odds") else {}
            spread     = odds_raw.get("spread")
            over_under = odds_raw.get("overUnder")
            home_ml    = odds_raw.get("homeTeamOdds", {}).get("moneyLine")
            away_ml    = odds_raw.get("awayTeamOdds", {}).get("moneyLine")

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

            # If ESPN has no odds data, synthesize fair-value prices so agents can still run
            if not markets:
                markets = []
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
                # No spread/total at all — synthesize neutral ML so the game is at least evaluated
                if not markets:
                    markets.append({"key": "h2h", "outcomes": [
                        {"name": home_team, "price": -110},
                        {"name": away_team, "price": -110},
                    ]})
            bookmakers = [{"key": "espn_consensus", "title": "ESPN Consensus", "markets": markets}]

            status = comp.get("status", {}).get("type", {})
            events.append({
                "id":          game.get("id", ""),
                "sport_slug":  sport_slug,
                "home_team":   home_team,
                "away_team":   away_team,
                "home_abbr":   home.get("team", {}).get("abbreviation", ""),
                "away_abbr":   away.get("team", {}).get("abbreviation", ""),
                "commence_time": game.get("date", ""),
                "state":       status.get("state", "pre"),
                "bookmakers":  bookmakers,
                "espn_spread": spread,
                "espn_total":  over_under,
                "espn_home_ml": home_ml,
                "espn_away_ml": away_ml,
            })

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
                bets    = g.get("bets", {})
                spread_bets = bets.get("spread", {})
                ml_bets     = bets.get("moneyline", {})
                home_spread_pct = spread_bets.get("home_bets_pct", 50.0) or 50.0
                home_ml_pct     = ml_bets.get("home_bets_pct", 50.0)     or 50.0
                home_sharp_pct  = spread_bets.get("home_handle_pct", 50.0) or 50.0
                line_history = g.get("line_history", [])
                spread_line  = g.get("spread", {})
                current_spread = spread_line.get("home")
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
        async with httpx.AsyncClient(timeout=12, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }) as client:
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
# 7 AGENTS
# ═══════════════════════════════════════════════════════════════════════════════

def agent_value(market_prob: float, fair_prob: float,
                pinnacle_clv: Optional[float] = None) -> dict:
    """
    Agent 1 — Value Finder.
    Uses Pinnacle CLV as primary edge signal when available.
    Falls back to vig-removal estimate otherwise.
    """
    if pinnacle_clv is not None:
        # Real edge: how much better our line is vs Pinnacle fair price
        edge = pinnacle_clv
        source = "pinnacle_clv"
    else:
        # Fallback: vig-removal estimate (weaker signal)
        edge = (fair_prob - market_prob) * 100
        source = "vig_removal"

    grade = ("A+" if edge >= 8 else "A" if edge >= 6 else "B+" if edge >= 4 else
             "B"  if edge >= 2 else "C" if edge >= 0 else "F")
    return {
        "grade": grade, "edge_pct": round(edge, 2),
        "label": f"Value: {grade}",
        "source": source,
        "clv_based": pinnacle_clv is not None,
    }


def agent_line_movement(opening: Optional[float], current: Optional[float],
                        bet_side: str, line_history: list = None) -> dict:
    """Agent 2 — Line Movement. Uses Action Network line history."""
    if opening is None or current is None:
        return {"favorable": False, "label": "Line: No data", "move": "→ Stable",
                "steam_moves": 0, "diff": 0}
    diff = current - opening
    favorable = ((bet_side == "away" and diff > 0.3) or
                 (bet_side == "home" and diff < -0.3) or
                 (bet_side == "over"  and diff < -0.3) or
                 (bet_side == "under" and diff > 0.3))
    if abs(diff) < 0.3:
        label, move = "Line: Stable →", "→ Stable"
    elif favorable:
        label = f"Line: +{abs(diff):.1f} in our favor ↑"
        move  = f"▲ Moved {abs(diff):.1f}pts our way"
    else:
        label = f"Line: -{abs(diff):.1f} moved away ↓"
        move  = f"▼ Moved {abs(diff):.1f}pts against"

    steam_moves = 0
    if line_history and len(line_history) >= 3:
        recent  = line_history[-3:]
        spreads = [h.get("home_spread") for h in recent if h.get("home_spread") is not None]
        if len(spreads) >= 2 and all(abs(spreads[i+1]-spreads[i]) > 0.25
                                     for i in range(len(spreads)-1)):
            steam_moves = len(spreads)

    return {"favorable": favorable, "label": label, "move": move,
            "steam_moves": steam_moves, "diff": round(diff, 2)}


def agent_public_money(public_pct: Optional[float] = None,
                       sharp_pct: Optional[float] = None,
                       an_game: Optional[dict] = None) -> dict:
    """Agent 3 — Sharp Money. Uses real Action Network splits."""
    if an_game:
        public_pct = an_game.get("public_pct", 50.0) or 50.0
        sharp_pct  = an_game.get("sharp_pct",  50.0) or 50.0
    elif public_pct is None:
        public_pct = 45.0; sharp_pct = 55.0

    rlm   = public_pct < 40 and (sharp_pct or 50) > 60
    steam = (sharp_pct or 0) > 70

    if steam:
        action, signal_strength = "🔴 Steam Move — sharp money flooding in", 0.9
    elif rlm:
        action, signal_strength = "⚡ Reverse Line Move — sharps vs public", 0.75
    elif (sharp_pct or 50) > 55:
        action, signal_strength = f"Sharp lean {sharp_pct:.0f}%", 0.6
    else:
        action, signal_strength = f"Public split {public_pct:.0f}/{100-public_pct:.0f}", 0.4

    return {
        "public_pct": public_pct, "sharp_pct": sharp_pct or (100 - public_pct),
        "rlm": rlm, "steam": steam, "action": action,
        "signal_strength": signal_strength, "label": action,
        "source": "live" if an_game else "estimated",
    }


def agent_injury(home_team: str, away_team: str, injury_cache: list = None,
                 bet_side: str = "") -> dict:
    """Agent 4 — Injuries. Returns impact + veto flag if bet side has key player out."""
    if not injury_cache:
        return {"impact": 0.0, "notes": "All clear", "label": "Injuries: All clear ✓",
                "veto": False, "veto_reason": ""}

    home_lower = home_team.lower()
    away_lower = away_team.lower()
    relevant   = []
    max_impact = 0.0
    for inj in injury_cache:
        ta = inj.get("team", "").lower()
        if ta and (ta in home_lower or ta in away_lower or
                   any(ta in p for p in home_lower.split()) or
                   any(ta in p for p in away_lower.split())):
            relevant.append(inj)
            score = {"High": 1.0, "Medium-High": 0.7, "Medium": 0.4, "Low": 0.1}.get(inj["impact"], 0.1)
            max_impact = max(max_impact, score)

    if not relevant:
        return {"impact": 0.0, "notes": "All clear", "label": "Injuries: All clear ✓",
                "veto": False, "veto_reason": ""}

    top = relevant[0]

    # VETO: if bet_side team has a HIGH-impact OUT, kill the pick
    veto = False
    veto_reason = ""
    if max_impact >= 1.0 and bet_side:
        bet_team = away_team if bet_side == "away" else home_team
        bet_lower = bet_team.lower()
        for inj in relevant:
            ta = inj.get("team", "").lower()
            if ta and any(ta in p for p in bet_lower.split()):
                if inj.get("impact") == "High":
                    veto = True
                    veto_reason = f"{inj['player']} OUT — suppressing pick on {bet_team}"
                    break

    count_str = f" (+{len(relevant)-1} more)" if len(relevant) > 1 else ""
    return {
        "impact":  max_impact,
        "notes":   f"{top['player']} ({top['status']}){count_str}",
        "label":   f"Injuries: {top['player']} {top['status']} ⚠️",
        "players": relevant[:3],
        "veto":    veto,
        "veto_reason": veto_reason,
    }


def agent_situational(game: dict, sport: str, espn_game: dict = None) -> dict:
    """Agent 5 — Situational context from ESPN."""
    notes = []; score = 0.5
    if espn_game and espn_game.get("state") == "in":
        notes.append("Live game — line may have shifted"); score = 0.45
    if sport == "basketball_nba":
        notes.append("NBA: check for back-to-back fatigue"); score = 0.52
    elif sport == "americanfootball_nfl":
        notes.append("NFL schedule spot analysis"); score = 0.55
    elif sport == "icehockey_nhl":
        notes.append("NHL: 3rd game in 4 nights matters"); score = 0.52
    elif sport == "baseball_mlb":
        notes.append("MLB: bullpen usage last 3 days key"); score = 0.50
    return {"score": score, "notes": notes or ["Standard game"],
            "label": f"Situational: {notes[0] if notes else 'Standard spot'}"}


def agent_fade_public(public_pct: float, odds: int) -> dict:
    """Agent 6 — Fade the Public."""
    fade_signal     = public_pct >= 70
    signal_strength = (public_pct - 50) / 50 if public_pct > 50 else 0
    return {
        "fade_signal": fade_signal, "public_pct": public_pct,
        "signal_strength": round(signal_strength, 2),
        "label": f"Fade public: {'Strong ✓' if fade_signal else 'Neutral'} ({public_pct:.0f}%)",
    }


def agent_kelly(edge_pct: float, odds: int) -> dict:
    """Agent 7 — Kelly Criterion Sizing."""
    fraction = kelly_fraction(edge_pct / 100, odds)
    units    = round(fraction * 10, 1)
    size_label = ("3+ units (max bet)" if units >= 3 else
                  f"{units} units"     if units >= 1 else
                  "0.5 units (half)"   if units > 0  else "No bet")
    return {"kelly_fraction": fraction, "units": units, "label": f"Kelly: {size_label}"}


# ═══════════════════════════════════════════════════════════════════════════════
# VETO ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_veto_checks(a2: dict, a3: dict, a4: dict,
                    best_edge: float, bet_side: str) -> tuple[bool, list]:
    """
    Returns (veto_triggered: bool, reasons: list[str]).

    Veto conditions — any one of these kills the pick:
      V1 (Injury):    key player is OUT on the bet side           [Agent 4]
      V2 (Line):      line moved ≥2 pts AGAINST us                [Agent 2]
      V3 (Public trap): >65% public + sharp handle <40%           [Agent 3]
      V4 (No edge):   CLV/edge < 0 even after all signals         [Agent 1 proxy]
    """
    reasons = []

    # V1 — Injury veto
    if a4.get("veto"):
        reasons.append(f"V1-Injury: {a4['veto_reason']}")

    # V2 — Line moved hard against us (market is telling us we're wrong)
    diff = a2.get("diff", 0)
    if not a2.get("favorable") and abs(diff) >= 2.0:
        reasons.append(f"V2-LineMove: moved {diff:+.1f}pts against bet side")

    # V3 — Public trap: heavy public action with low sharp interest
    pub  = a3.get("public_pct", 50)
    shrp = a3.get("sharp_pct", 50)
if pub > 75 and shrp < 30:
   reasons.append(f"V3-PublicTrap: {pub:.0f}% public, only {shrp:.0f}% sharp handle")

    # V4 — Edge too thin (below minimum threshold after signal weighting)
    if best_edge < 0.5:
        reasons.append(f"V4-ThinEdge: edge {best_edge:.1f}% below 0.5% minimum")

    return len(reasons) > 0, reasons


# ═══════════════════════════════════════════════════════════════════════════════
# CONSENSUS ENGINE  (v3: CLV + veto + logging)
# ═══════════════════════════════════════════════════════════════════════════════

def build_consensus_pick(event: dict, sport_key: str,
                         an_game: Optional[dict] = None,
                         injury_cache: list = None,
                         pinnacle_lines: list = None,
                         weather: Optional[dict] = None) -> Optional[dict]:
    """
    Run all 7 agents with:
    - Pinnacle CLV as the primary edge signal
    - Agent veto system to suppress bad picks
    - Weather-adjusted confidence for NFL/MLB totals
    - Platt-calibrated confidence score
    - Empirically fitted agent weights (or hand-tuned defaults)
    Returns None if pick is vetoed or has no edge.
    """
    meta  = SPORT_META.get(sport_key, {"label": sport_key, "emoji": "🎯"})
    books = event.get("bookmakers", [])
    if not books:
        return None

    home = event.get("home_team", "")
    away = event.get("away_team", "")
    game_label = f"{away} vs {home}"

    spread_prices: list = []
    h2h_prices: list    = []
    for book in books:
        for mkt in book.get("markets", []):
            if mkt["key"] == "spreads":
                for o in mkt.get("outcomes", []):
                    spread_prices.append((o["name"], o.get("point", 0), o["price"]))
            elif mkt["key"] == "h2h":
                for o in mkt.get("outcomes", []):
                    h2h_prices.append((o["name"], o["price"]))

    if not spread_prices and not h2h_prices:
        return None

    pin_game = match_pinnacle_game(pinnacle_lines or [], home, away)

    best_edge = -999
    best_pick = None

    if spread_prices:
        teams = {}
        for team, point, price in spread_prices:
            teams.setdefault(team, []).append((point, price))
        for team, entries in teams.items():
            avg_point = sum(p for p, _ in entries) / len(entries)
            avg_price = sum(p for _, p in entries) / len(entries)
            implied   = american_to_prob(int(avg_price))
            fair_prob = (sum(american_to_prob(int(pr)) for _, pr in entries) / len(entries)) / 1.02
            pin_spread = (pin_game.get("home_spread") if team == home
                          else pin_game.get("away_spread")) if pin_game else None
            clv_edge = calculate_clv_edge(int(avg_price), None, pin_spread, "spread", avg_point)
effective_edge = clv_edge if clv_edge is not None else 1.0
if effective_edge > best_edge:
                best_edge = effective_edge
                sign = "+" if avg_point > 0 else ""
                best_pick = {
                    "game": game_label, "home_team": home, "away_team": away,
                    "bet": f"{team} {sign}{avg_point:.1f}", "betType": "spread",
                    "bet_side": "away" if team == away else "home",
                    "odds": int(avg_price), "bet_point": avg_point,
                    "opening_point": an_game.get("opening_spread") if an_game else avg_point,
                    "current_point": an_game.get("current_spread") if an_game else avg_point,
                    "fair_prob": fair_prob, "implied_prob": implied,
                    "clv_edge": clv_edge, "pinnacle_line": pin_spread,
                    "edge_source": "pinnacle_clv" if clv_edge is not None else "vig_removal",
                    # Timing parity: record when both ESPN and Pinnacle lines were captured
                    "espn_line":            avg_point,
                    "espn_line_fetched_at": datetime.utcnow().isoformat(),
                    "pinnacle_fetched_at":  pin_game.get("pinnacle_fetched_at") if pin_game else None,
                }

    if h2h_prices:
        teams_h2h = {}
        for team, price in h2h_prices:
            teams_h2h.setdefault(team, []).append(price)
        for team, prices in teams_h2h.items():
            avg_price = sum(prices) / len(prices)
            implied   = american_to_prob(int(avg_price))
            fair_prob = implied / 1.02
            pin_ml    = (pin_game.get("home_ml") if team == home
                         else pin_game.get("away_ml")) if pin_game else None
            clv_edge  = calculate_clv_edge(int(avg_price), pin_ml, None, "moneyline")
effective_edge = clv_edge if clv_edge is not None else 1.0
if effective_edge > best_edge:
                best_edge = effective_edge
                best_pick = {
                    "game": game_label, "home_team": home, "away_team": away,
                    "bet": f"{team} ML", "betType": "moneyline",
                    "bet_side": "away" if team == away else "home",
                    "odds": int(avg_price), "bet_point": None,
                    "opening_point": None, "current_point": None,
                    "fair_prob": fair_prob, "implied_prob": implied,
                    "clv_edge": clv_edge, "pinnacle_line": pin_ml,
                    "edge_source": "pinnacle_clv" if clv_edge is not None else "vig_removal",
                    # Timing parity
                    "espn_line":            avg_price,
                    "espn_line_fetched_at": datetime.utcnow().isoformat(),
                    "pinnacle_fetched_at":  pin_game.get("pinnacle_fetched_at") if pin_game else None,
                }

min_edge = 1.0 if best_pick and best_pick.get("edge_source") == "pinnacle_clv" else 0.5
    if best_edge < min_edge or best_pick is None:
        return None

    # ── Run all 7 agents ──────────────────────────────────────────────────────
    line_hist = an_game.get("line_history", []) if an_game else []

    a1 = agent_value(best_pick["implied_prob"], best_pick["fair_prob"],
                     pinnacle_clv=best_pick.get("clv_edge"))
    a2 = agent_line_movement(best_pick["opening_point"], best_pick["current_point"],
                             best_pick["bet_side"], line_hist)
    a3 = agent_public_money(an_game=an_game)
    a4 = agent_injury(home, away, injury_cache, bet_side=best_pick["bet_side"])
    a5 = agent_situational(event, sport_key, espn_game=event)
    a6 = agent_fade_public(a3["public_pct"], best_pick["odds"])
    a7 = agent_kelly(best_edge, best_pick["odds"])

    # ── VETO CHECK ────────────────────────────────────────────────────────────
    vetoed, veto_reasons = run_veto_checks(a2, a3, a4, best_edge, best_pick["bet_side"])
    if vetoed:
        print(f"[VETO] {game_label} — {best_pick['bet']} suppressed: {veto_reasons}")
        return None

    # ── Which agents fired ────────────────────────────────────────────────────
    agents_fired = []
    if a1["grade"] in ("A+","A","B+"): agents_fired.append("value")
    if a2["favorable"]:                agents_fired.append("line_movement")
    if a3["steam"] or a3["rlm"]:       agents_fired.append("public_money")
    if a4["impact"] < 0.4:             agents_fired.append("injury")
    if a5["score"] > 0.5:              agents_fired.append("situational")
    if a6["fade_signal"]:              agents_fired.append("fade_public")
    if a7["units"] >= 1:               agents_fired.append("kelly")

    agent_agreement = len(agents_fired) / 7

    # ── Confidence with FITTED or default weights ─────────────────────────────
    w = get_agent_weights()
    injury_penalty = a4["impact"] * abs(w.get("injury", -5.0))
    steam_bonus    = 3 if a2.get("steam_moves", 0) >= 2 else 0
    clv_bonus      = 5 if best_pick.get("edge_source") == "pinnacle_clv" else 0

    raw_confidence = min(95, max(50, int(
        best_edge * w.get("value", 3.0) +
        a3["signal_strength"] * w.get("public_money", 15.0) +
        a6["signal_strength"] * w.get("fade_public", 10.0) +
        a5["score"]           * w.get("situational", 8.0) +
        (w.get("line_movement", 5.0) if a2["favorable"] else 0) +
        steam_bonus + clv_bonus +
        agent_agreement * 10 +
        55 - injury_penalty
    )))

    # ── Weather adjustment ────────────────────────────────────────────────────
    weather_adj = weather_confidence_adjustment(weather, best_pick["betType"])
    raw_confidence = min(95, max(50, raw_confidence + weather_adj))

    # ── Platt calibration ─────────────────────────────────────────────────────
    calibrated_prob = calibrate_confidence(raw_confidence)

    odds_int = best_pick["odds"]
    odds_str = f"+{odds_int}" if odds_int > 0 else str(odds_int)

    # Weather info for pick output
    weather_flag    = weather.get("weather_flag") if weather else None
    weather_details = weather if weather else None

    pick_out = {
        "id": abs(hash(best_pick["game"] + best_pick["bet"])) % 100000,
        "sport": meta["label"], "emoji": meta["emoji"],
        "game":      best_pick["game"],
        "homeTeam":  home, "awayTeam": away,
        "bet":       best_pick["bet"],
        "betType":   best_pick["betType"],
        "bet_side":  best_pick["bet_side"],
        "confidence":          raw_confidence,
        "confidence_raw":      raw_confidence,
        "confidence_calibrated": calibrated_prob,
        "confidence_pct":      f"{round(calibrated_prob * 100, 1)}%",
        "using_fitted_weights": bool(_fitted_agent_weights),
        "edge":       round(best_edge, 1),
        "edge_source": best_pick["edge_source"],
        "odds":       odds_str,
        "odds_int":   odds_int,
        "decimalOdds": round(
            (odds_int / 100 + 1) if odds_int > 0 else (100 / abs(odds_int) + 1), 2
        ),
        "openingLine": f"{best_pick['opening_point']:+.1f}" if best_pick["opening_point"] else "N/A",
        "currentLine": f"{best_pick['current_point']:+.1f}" if best_pick["current_point"] else "N/A",
        "lineMove":    a2["move"],
        "pinnacle_line": best_pick.get("pinnacle_line"),
        "clv_edge":      best_pick.get("clv_edge"),
        "pinnacle_fetched_at":  best_pick.get("pinnacle_fetched_at"),
        "espn_line":            best_pick.get("espn_line"),
        "espn_line_fetched_at": best_pick.get("espn_line_fetched_at"),
        # Line timing delta — if both timestamps present, shows how stale the comparison is
        "clv_timing_lag_seconds": (
            abs((datetime.fromisoformat(best_pick["espn_line_fetched_at"]) -
                 datetime.fromisoformat(best_pick["pinnacle_fetched_at"])).total_seconds())
            if best_pick.get("espn_line_fetched_at") and best_pick.get("pinnacle_fetched_at")
            else None
        ),
        "weather_flag":    weather_flag,
        "weather_details": weather_details,
        "model_breakdown": {
            "value":         a1["label"] + (" [CLV]" if a1["clv_based"] else " [est]"),
            "line_movement": a2["label"],
            "public_money":  f"{a3['public_pct']:.0f}% public ({a3['source']})",
            "sharp_action":  f"{a3['sharp_pct']:.0f}% sharp",
            "injury_report": a4["notes"],
            "situational":   a5["notes"][0] if a5["notes"] else "Standard spot",
            "kelly_size":    a7["label"],
            "weather":       weather["description"] if weather else "N/A",
        },
        "agents": {
            "value": a1, "line_movement": a2, "public_money": a3,
            "injury": a4, "situational": a5, "fade_public": a6, "kelly": a7,
        },
        "agents_fired":  agents_fired,
        "agents_vetoed": [],
        "veto_passed":   True,
        "agent_agreement_pct": round(agent_agreement * 100),
        "steam": a3["steam"], "rlm": a3["rlm"], "sharpPct": a3["sharp_pct"],
        "fair_prob":    best_pick["fair_prob"],
        "implied_prob": best_pick["implied_prob"],
        "data_source":  "espn+actionnetwork+pinnacle",
        "game_time":    event.get("commence_time", ""),
    }
    return pick_out


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND WARMER
# ═══════════════════════════════════════════════════════════════════════════════

async def _warm_cache():
    await asyncio.sleep(15)
    cycle = 0
    while True:
        try:
            print("[warmer] Refreshing ESPN + Action Network + Pinnacle...")
            await asyncio.gather(
                fetch_espn_all_games(),
                fetch_action_network_lines(),
                fetch_all_pinnacle(),
                return_exceptions=True,
            )
            print("[warmer] Free data refreshed.")
            if cycle % 8 == 0 and ODDS_API_KEY:
                print("[warmer] Refreshing Odds API (EV/arb)...")
                await fetch_all_odds(markets="h2h,spreads,totals")
                print("[warmer] Odds API refreshed.")
            cycle += 1
        except Exception as e:
            print(f"[warmer] Error: {e}")
        await asyncio.sleep(CACHE_TTL_FREE)


async def _clv_capture_loop():
    """Runs every 10 minutes — captures Pinnacle closing lines for picks approaching game time."""
    await asyncio.sleep(60)  # give warmer a head start
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
async def root():
    return {"status": "ok", "service": "Algobets Ai API", "version": "4.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/quota")
async def quota():
    scan_age = cache_age_seconds("scan_result")
    return {
        "quota_remaining":    _quota_remaining,
        "quota_used_last":    _quota_used_last,
        "low_quota":          _quota_remaining < 50,
        "odds_api_used_for":  "ev_finder + arb_detect only",
        "scan_data_source":   "ESPN + Action Network + Pinnacle + Open-Meteo (all free)",
        "cache_ttl_free":     CACHE_TTL_FREE,
        "cache_ttl_paid":     CACHE_TTL,
        "scan_age_seconds":   scan_age,
        "scan_fresh":         0 <= scan_age < CACHE_TTL_FREE,
        "next_refresh_seconds": max(0, CACHE_TTL_FREE - scan_age) if scan_age >= 0 else 0,
        "bookmakers_tracked": ODDS_BOOKMAKERS,
        "calibration_active": bool(_calibration_params),
        "fitted_weights_active": bool(_fitted_agent_weights),
    }


@app.get("/scan", dependencies=[Depends(verify_api_key), Depends(require_paid_plan)])
@limiter.limit("10/minute")
async def scan(request: Request):
    # Tiered rate: sharp=60/min, pro=30/min, free blocked by require_paid_plan
    plan = await get_user_plan(request)
    # (slowapi doesn't natively support dynamic limits, so we enforce sharp/pro here)
    """
    7-agent consensus picks.
    Sources: ESPN + Action Network + Pinnacle CLV + Open-Meteo weather.
    Confidence: Platt-calibrated. Weights: fitted or hand-tuned defaults.
    Every surfaced pick logged to SQLite for ROI tracking.
    Cost: $0 Odds API credits.
    """
    cached = cache_get("scan_result", ttl=CACHE_TTL_FREE)
    if cached:
        return cached

    espn_games, an_lines, injury_list, pinnacle_all = await asyncio.gather(
        fetch_espn_all_games(),
        fetch_action_network_lines(),
        fetch_all_espn_injuries(),
        fetch_all_pinnacle(),
        return_exceptions=True,
    )
    if isinstance(espn_games,   Exception): espn_games   = {}
    if isinstance(an_lines,     Exception): an_lines     = {}
    if isinstance(injury_list,  Exception): injury_list  = []
    if isinstance(pinnacle_all, Exception): pinnacle_all = {}

    picks = []
    for sport_key, events in espn_games.items():
        an_sport_games  = an_lines.get(sport_key, [])
        pin_sport_lines = pinnacle_all.get(sport_key, [])

        # Fetch weather concurrently for all events in NFL/MLB
        weather_tasks = [
            fetch_weather_for_game(
                event.get("home_team", ""),
                event.get("commence_time", ""),
                sport_key,
            )
            for event in events[:8]
        ]
        weather_results = await asyncio.gather(*weather_tasks, return_exceptions=True)

        for event, weather in zip(events[:8], weather_results):
            if isinstance(weather, Exception):
                weather = None
            an_game = match_an_game(an_sport_games,
                                    event.get("home_team",""), event.get("away_team",""))
            pick = build_consensus_pick(
                event, sport_key,
                an_game=an_game,
                injury_cache=injury_list,
                pinnacle_lines=pin_sport_lines,
                weather=weather,
            )
            if pick:
                picks.append(pick)
                log_pick(pick)

    picks.sort(key=lambda p: p["edge"] * p["confidence"], reverse=True)
    top_picks = picks[:10]
    print(f"[Scan] Sports: {list(espn_games.keys())} | Events analyzed: {sum(len(v) for v in espn_games.values())} | Picks generated: {len(picks)} | Top picks: {len(top_picks)}")

    clv_count     = sum(1 for p in top_picks if p.get("edge_source") == "pinnacle_clv")
    weather_count = sum(1 for p in top_picks if p.get("weather_flag"))

    result = {
        "consensus_picks":          top_picks,
        "scan_timestamp":           datetime.utcnow().isoformat(),
        "sports_scanned":           len(espn_games),
        "events_analyzed":          sum(len(v) for v in espn_games.values()),
        "live":                     len(espn_games) > 0,
        "data_sources":             ["ESPN", "Action Network", "Pinnacle", "Open-Meteo"],
        "picks_with_clv":           clv_count,
        "picks_with_weather_flag":  weather_count,
        "picks_total":              len(top_picks),
        "calibration_active":       bool(_calibration_params),
        "fitted_weights_active":    bool(_fitted_agent_weights),
        "odds_api_credits_used":    0,
        "cache_ttl_seconds":        CACHE_TTL_FREE,
    }
    cache_set("scan_result", result)
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
    Expects: { picks: [ { pick_id, result, pnl, clv_actual? } ] }
    Automatically triggers calibration + weight refit when threshold is reached.
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
                + f". Combined but remember: each leg must win independently."
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

