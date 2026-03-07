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
import base64
import hmac
import secrets
import urllib.parse
import httpx
import asyncio
import hashlib
import stripe
import numpy as np
import statistics
from datetime import datetime, timedelta
from typing import Optional, Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

ODDS_API_KEY    = os.getenv("ODDS_API_KEY", "").strip()
STRIPE_SECRET   = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK  = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
FRONTEND_URL    = os.getenv("FRONTEND_URL", "https://algobets.ai").strip()
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "").strip()
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
JWT_ISSUER = os.getenv("JWT_ISSUER", "").strip()
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "").strip()
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/New_York").strip() or "America/New_York"
VIP_DISCORD_URL = os.getenv("VIP_DISCORD_URL", "").strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

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


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


PREMIUM_PRICE_IDS = _csv_env("STRIPE_PREMIUM_PRICE_IDS") or _csv_env("STRIPE_PRO_PRICE_IDS")
VIP_PRICE_IDS = _csv_env("STRIPE_VIP_PRICE_IDS") or _csv_env("STRIPE_SHARP_PRICE_IDS")
PREMIUM_ANNUAL_PRICE_IDS = _csv_env("STRIPE_PREMIUM_ANNUAL_PRICE_IDS")
VIP_ANNUAL_PRICE_IDS = _csv_env("STRIPE_VIP_ANNUAL_PRICE_IDS")
ALLOWED_ORIGINS = [o for o in (_csv_env("ALLOWED_ORIGINS") or [FRONTEND_URL]) if o]
BILLING_RETURN_ORIGINS = [o.rstrip("/").lower() for o in (_csv_env("BILLING_RETURN_ORIGINS") or ALLOWED_ORIGINS) if o]
ENFORCE_ORIGIN_CHECKS = _bool_env("ENFORCE_ORIGIN_CHECKS", "true")
REQUIRE_BACKEND_API_KEY = _bool_env("REQUIRE_BACKEND_API_KEY", "true") and bool(BACKEND_API_KEY)
SECURITY_HEADERS_ENABLED = _bool_env("SECURITY_HEADERS_ENABLED", "true")
DEBUG_ENDPOINTS_PUBLIC = _bool_env("DEBUG_ENDPOINTS_PUBLIC", "false")
WEBHOOK_EVENT_TTL_SECONDS = int(os.getenv("WEBHOOK_EVENT_TTL_SECONDS", str(7 * 24 * 3600)) or (7 * 24 * 3600))
REQUIRE_AUTH_TOKEN = _bool_env("REQUIRE_AUTH_TOKEN", "true")
COMMUNITY_ENABLED = _bool_env("COMMUNITY_ENABLED", "true")
BILLING_ENABLED = _bool_env("BILLING_ENABLED", "true")
SCAN_ENABLED = _bool_env("SCAN_ENABLED", "true")
CHECKOUT_MAX_PER_HOUR = int(os.getenv("CHECKOUT_MAX_PER_HOUR", "6") or 6)
TRIAL_MAX_PER_DAY = int(os.getenv("TRIAL_MAX_PER_DAY", "2") or 2)
WAITLIST_MAX_PER_HOUR = int(os.getenv("WAITLIST_MAX_PER_HOUR", "10") or 10)
SECURITY_EVENT_CAP = int(os.getenv("SECURITY_EVENT_CAP", "5000") or 5000)
AUTH_TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", str(30 * 24 * 3600)) or (30 * 24 * 3600))

if not JWT_SECRET:
    JWT_SECRET = secrets.token_hex(32)
    print("[Security] JWT_SECRET not set. Generated ephemeral secret for this process.")


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
    if not REQUIRE_BACKEND_API_KEY:
        return
    if not BACKEND_API_KEY or x_api_key != BACKEND_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key.")


def _b64url_decode(segment: str) -> bytes:
    s = segment + "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode(s.encode("utf-8"))

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _json_b64url_decode(segment: str) -> dict[str, Any]:
    raw = _b64url_decode(segment)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JWT payload must be an object.")
    return data


def _verify_hs256_jwt(token: str) -> dict[str, Any]:
    if not JWT_SECRET:
        raise ValueError("JWT secret is not configured.")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT.")
    h, p, s = parts
    header = _json_b64url_decode(h)
    payload = _json_b64url_decode(p)
    if str(header.get("alg", "")).upper() != "HS256":
        raise ValueError("Unsupported JWT algorithm.")
    signing_input = f"{h}.{p}".encode("utf-8")
    expected_sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_sig = _b64url_decode(s)
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise ValueError("Invalid JWT signature.")
    now = int(time.time())
    exp = payload.get("exp")
    nbf = payload.get("nbf")
    if exp is not None and int(exp) < now:
        raise ValueError("JWT expired.")
    if nbf is not None and int(nbf) > now:
        raise ValueError("JWT not active yet.")
    if JWT_ISSUER and str(payload.get("iss", "")).strip() != JWT_ISSUER:
        raise ValueError("JWT issuer mismatch.")
    if JWT_AUDIENCE:
        aud = payload.get("aud")
        if isinstance(aud, list):
            if JWT_AUDIENCE not in [str(x) for x in aud]:
                raise ValueError("JWT audience mismatch.")
        elif str(aud) != JWT_AUDIENCE:
            raise ValueError("JWT audience mismatch.")
    return payload


def _normalize_user_id(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if not re.fullmatch(r"[a-z0-9:_-]{6,64}", raw):
        return ""
    return raw


def _issue_hs256_jwt(subject: str, ttl_seconds: int = AUTH_TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    exp = now + max(300, int(ttl_seconds or AUTH_TOKEN_TTL_SECONDS))
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": subject, "iat": now, "exp": exp}
    if JWT_ISSUER:
        payload["iss"] = JWT_ISSUER
    if JWT_AUDIENCE:
        payload["aud"] = JWT_AUDIENCE
    h = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    p = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(JWT_SECRET.encode("utf-8"), f"{h}.{p}".encode("utf-8"), hashlib.sha256).digest()
    s = _b64url_encode(sig)
    return f"{h}.{p}.{s}"


# ═══════════════════════════════════════════════════════════════════════════════
# PLAN VERIFICATION (Stripe)
# ═══════════════════════════════════════════════════════════════════════════════

_plan_cache: dict = {}
_PLAN_CACHE_TTL = 300
_scan_state: dict = {}
_SCAN_STATE_TTL = 3600 * 24

def _get_billing_entitlement(user_id: str) -> str:
    if not user_id:
        return PLAN_FREE
    rows = _growth_db.get("billing_entitlements", {})
    if not isinstance(rows, dict):
        return PLAN_FREE
    rec = rows.get(user_id)
    if not isinstance(rec, dict):
        return PLAN_FREE
    return normalize_plan_name(rec.get("plan", PLAN_FREE))


def _set_billing_entitlement(user_id: str, plan: str, source: str = "stripe_webhook"):
    if not user_id:
        return
    plan_norm = normalize_plan_name(plan)
    rows = _growth_db.setdefault("billing_entitlements", {})
    if not isinstance(rows, dict):
        rows = {}
    rows[user_id] = {"plan": plan_norm, "updated_at": int(time.time()), "source": str(source or "stripe_webhook")[:48]}
    _growth_db["billing_entitlements"] = rows
    _save_growth_db()


def _verify_plan_stripe_sync(user_id: str) -> str:
    return _get_billing_entitlement(user_id)


OWNER_EMAILS = {"grandrichlife727@gmail.com"}

async def _get_verified_plan(request: Request) -> str:
    user_id = _request_user_id(request)
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

CACHE_TTL          = int(os.getenv("CACHE_TTL", "1800") or 1800)
CACHE_TTL_PINNACLE = int(os.getenv("CACHE_TTL_PINNACLE", "1800") or 1800)
CACHE_TTL_INJURIES = int(os.getenv("CACHE_TTL_INJURIES", "900") or 900)
ODDS_MIN_REMAINING_TO_SCAN = int(os.getenv("ODDS_MIN_REMAINING_TO_SCAN", "10") or 10)
MODEL_MIN_EDGE_EV = float(os.getenv("MODEL_MIN_EDGE_EV", "1.0") or 1.0)
MODEL_MIN_BOOKS = int(os.getenv("MODEL_MIN_BOOKS", "3") or 3)
MODEL_MIN_CONFIDENCE = int(os.getenv("MODEL_MIN_CONFIDENCE", "58") or 58)
MODEL_MAX_PICKS_PER_SPORT = int(os.getenv("MODEL_MAX_PICKS_PER_SPORT", "40") or 40)

ODDS_BOOKMAKERS = os.getenv("ODDS_BOOKMAKERS", "draftkings,fanduel,betmgm,pinnacle,williamhill_us,bovada")
PROPS_BOOKMAKERS = os.getenv("PROPS_BOOKMAKERS", "draftkings,fanduel,betmgm")
PROPS_ENABLED = os.getenv("PROPS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
PROPS_MONTHLY_CREDIT_CAP = int(os.getenv("PROPS_MONTHLY_CREDIT_CAP", "70000") or 70000)
PROPS_MAX_EVENTS_PER_SPORT = int(os.getenv("PROPS_MAX_EVENTS_PER_SPORT", "4") or 4)
PROPS_CACHE_TTL = int(os.getenv("PROPS_CACHE_TTL", "1800") or 1800)

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

PROPS_MARKETS_BY_SPORT: dict[str, list[str]] = {
    "basketball_nba": ["player_points", "player_rebounds"],
    "americanfootball_nfl": ["player_pass_yds", "player_rush_yds"],
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
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_allowed_origin_set = {o.rstrip("/").lower() for o in ALLOWED_ORIGINS if o}


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    if "*" in _allowed_origin_set:
        return True
    return origin.rstrip("/").lower() in _allowed_origin_set


def _is_sensitive_path(path: str) -> bool:
    if path.startswith("/api/billing/"):
        return True
    if path in {
        "/api/community/posts",
        "/api/alerts/subscribe",
        "/api/waitlist/join",
        "/api/referral/redeem",
        "/api/referral/status",
        "/api/trial/start",
        "/api/trial/status",
        "/api/profile/settings",
        "/api/history",
        "/api/alerts/subscriptions",
        "/api/digest/subscription",
        "/api/streak/status",
        "/api/streak/claim",
        "/api/quota",
        "/api/agents/weights",
        "/api/alerts/packs",
        "/api/affiliate/status",
        "/api/news-impact",
        "/api/bankroll/plan",
        "/api/journal",
        "/api/journal/export.csv",
        "/api/ev-finder",
        "/api/arb-detect",
        "/api/props",
        "/api/plan",
        "/scan",
    }:
        return True
    return False


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method.upper()
    is_write = method in {"POST", "PUT", "PATCH", "DELETE"}
    origin = request.headers.get("origin", "")
    auth_user_id = ""
    auth_ok = False

    auth_header = str(request.headers.get("authorization", "")).strip()
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        try:
            claims = _verify_hs256_jwt(token)
            auth_user_id = str(claims.get("sub") or claims.get("email") or claims.get("user_id") or "").strip().lower()
            auth_ok = bool(auth_user_id)
        except Exception as e:
            _audit_security_event(request, "auth.jwt_invalid", str(e))
    request.state.user_id = auth_user_id
    request.state.auth_verified = auth_ok

    if ENFORCE_ORIGIN_CHECKS and _is_sensitive_path(path) and is_write and origin and not _origin_allowed(origin):
        _audit_security_event(request, "security.origin_blocked", f"origin={origin}")
        return JSONResponse(status_code=403, content={"detail": "Origin not allowed."})

    if REQUIRE_AUTH_TOKEN and _is_sensitive_path(path):
        if not auth_ok:
            _audit_security_event(request, "security.auth_required", "Missing or invalid bearer token.")
            return JSONResponse(status_code=401, content={"detail": "Authentication required."})

    if REQUIRE_BACKEND_API_KEY and _is_sensitive_path(path) and path != "/api/billing/webhook":
        provided = request.headers.get("x-api-key", "")
        if not BACKEND_API_KEY or provided != BACKEND_API_KEY:
            _audit_security_event(request, "security.api_key_invalid", "Invalid x-api-key.")
            return JSONResponse(status_code=401, content={"detail": "Invalid API key."})

    response = await call_next(request)

    if SECURITY_HEADERS_ENABLED:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    return response


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


def _client_ip(request: Request) -> str:
    xff = str(request.headers.get("x-forwarded-for", "")).strip()
    if xff:
        return xff.split(",")[0].strip()
    xrip = str(request.headers.get("x-real-ip", "")).strip()
    if xrip:
        return xrip
    if request.client and request.client.host:
        return str(request.client.host)
    return "unknown"


def _audit_security_event(request: Request, event: str, detail: Optional[str] = None, user_id: Optional[str] = None):
    logs = _growth_db.setdefault("security_events", [])
    row = {
        "ts": int(time.time()),
        "event": str(event or "event")[:64],
        "path": str(request.url.path)[:200],
        "method": str(request.method)[:12],
        "ip": _client_ip(request)[:80],
        "user_id": str(user_id or _request_user_id(request) or "")[:120],
        "detail": str(detail or "")[:240],
    }
    logs.append(row)
    if len(logs) > SECURITY_EVENT_CAP:
        _growth_db["security_events"] = logs[-SECURITY_EVENT_CAP:]
    _save_growth_db()


def _velocity_allow(scope: str, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
    if limit <= 0:
        return True, 0, 0
    now = int(time.time())
    bucket = _growth_db.setdefault("velocity", {})
    rows = bucket.get(scope, [])
    if not isinstance(rows, list):
        rows = []
    rows = [r for r in rows if isinstance(r, dict) and int(r.get("ts", 0) or 0) >= (now - window_seconds)]
    count = len([r for r in rows if str(r.get("key", "")) == key])
    allow = count < limit
    if allow:
        rows.append({"key": key, "ts": now})
    bucket[scope] = rows[-20000:]
    _growth_db["velocity"] = bucket
    _save_growth_db()
    retry_after = 0
    if not allow:
        matching = [int(r.get("ts", now)) for r in rows if str(r.get("key", "")) == key]
        oldest = min(matching) if matching else now
        retry_after = max(1, window_seconds - (now - oldest))
    return allow, count + (1 if allow else 0), retry_after


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
    rec.setdefault("referral_bonus_pairs_awarded", 0)
    rec.setdefault("trial_until", 0)
    rec.setdefault("free_trial_claimed", False)
    rec.setdefault("free_trial_claimed_at", 0)
    rec.setdefault("community_post_timestamps", [])
    rec.setdefault("alerts", [])
    rec.setdefault("history", [])
    rec.setdefault("tracked_picks", [])
    rec.setdefault("streak_rewards_claimed", 0)
    rec.setdefault("digest", {"enabled": False, "channel": "email", "target": "", "hour_local": 9})
    rec.setdefault("profile", {"state": "auto", "bankroll_mode": "standard"})
    rec.setdefault("journal", [])
    rec.setdefault(
        "agent_weights",
        {"best_line_ev": 1.0, "market_consensus": 1.0, "devig": 1.0, "steam": 1.0, "fades": 1.0},
    )
    rec.setdefault("alert_packs", [])
    rec.setdefault("affiliate_clicks", [])
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
    referrals_count = len(rec.get("referrals", []))
    bonus_pairs_awarded = int(rec.get("referral_bonus_pairs_awarded", 0) or 0)
    next_bonus_at = (bonus_pairs_awarded + 1) * 2
    return {
        "referral_code": rec.get("referral_code"),
        "redeemed_code": rec.get("redeemed_code") or None,
        "referrals_count": referrals_count,
        "referrals_until_bonus": max(0, next_bonus_at - referrals_count),
        "trial_active_until": int(trial_until) if trial_until > now else None,
        "trial_seconds_left": secs_left,
        "free_trial_claimed": bool(rec.get("free_trial_claimed")),
    }


def _latest_cached_scan_payload(user_id: str = "") -> Optional[dict[str, Any]]:
    # Prefer the caller's last payload, then newest global cached scan payload.
    if user_id:
        row = _scan_state.get(user_id)
        if isinstance(row, dict) and isinstance(row.get("last_payload"), dict):
            return row.get("last_payload")
    latest_ts = -1.0
    latest_payload: Optional[dict[str, Any]] = None
    for row in _scan_state.values():
        if not isinstance(row, dict):
            continue
        payload = row.get("last_payload")
        ts = float(row.get("last_scan_ts", 0) or 0)
        if isinstance(payload, dict) and ts > latest_ts:
            latest_ts = ts
            latest_payload = payload
    return latest_payload


def _is_registered_user_id(user_id: str) -> bool:
    uid = (user_id or "").strip().lower()
    if not uid or uid.startswith("guest_"):
        return False
    return True


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
                "id": game.get("id"),
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


async def fetch_odds_api_scores(sport_key: str, days_from: int = 3) -> list[dict]:
    """Fetch completed game scores for settlement."""
    cache_key = f"scores_{sport_key}_{days_from}"
    cached = cache_get(cache_key, ttl=900)
    if cached is not None:
        return cached
    if not ODDS_API_KEY:
        return []
    odds_sport = ODDS_API_SPORT_MAP.get(sport_key, sport_key)
    url = f"{ODDS_BASE}/sports/{odds_sport}/scores"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                url,
                params={
                    "apiKey": ODDS_API_KEY,
                    "daysFrom": max(1, min(int(days_from), 7)),
                    "dateFormat": "iso",
                },
            )
            if r.status_code >= 400:
                return []
            payload = r.json()
            rows = payload if isinstance(payload, list) else []
            cache_set(cache_key, rows)
            return rows
    except Exception:
        return []


def _props_budget_ok() -> bool:
    if PROPS_MONTHLY_CREDIT_CAP <= 0:
        return True
    used = int(_quota_used_last or 0)
    if used <= 0:
        return True
    return used < PROPS_MONTHLY_CREDIT_CAP


def _rotated_markets_for_sport(sport_key: str) -> list[str]:
    base = list(PROPS_MARKETS_BY_SPORT.get(sport_key, []))
    if not base:
        return []
    # Rotate one primary market per hour to reduce request costs.
    hour = datetime.utcnow().hour
    idx = hour % len(base)
    return [base[idx]]


def _current_line_lookup(games: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for g in games:
        game_key = f"{g.get('away_team','')} @ {g.get('home_team','')}"
        lookup[game_key] = {
            "home_team": g.get("home_team"),
            "away_team": g.get("away_team"),
            "home_ml": g.get("home_ml"),
            "away_ml": g.get("away_ml"),
            "commence_time": g.get("commence_time"),
        }
    return lookup


async def fetch_props_lite_for_sport(
    sport_key: str,
    max_events: int = PROPS_MAX_EVENTS_PER_SPORT,
    markets: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Budget-safe props pull: only top upcoming events + limited markets/books."""
    global _quota_remaining, _quota_used_last
    allowed_markets = markets or _rotated_markets_for_sport(sport_key)
    if not allowed_markets:
        return []
    cache_key = f"props_lite_{sport_key}_{','.join(allowed_markets)}_{max_events}"
    cached = cache_get(cache_key, ttl=PROPS_CACHE_TTL)
    if cached is not None:
        return cached
    games = await fetch_odds_api_games(sport_key)
    if not games:
        return []
    now_iso = datetime.utcnow().isoformat() + "Z"
    upcoming = [g for g in games if str(g.get("commence_time") or "") >= now_iso and g.get("id")]
    upcoming.sort(key=lambda x: x.get("commence_time", ""))
    chosen = upcoming[: max(1, min(int(max_events), 8))]
    if not chosen:
        return []
    odds_sport = ODDS_API_SPORT_MAP.get(sport_key, sport_key)
    rows: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for g in chosen:
                if not _props_budget_ok():
                    break
                event_id = g.get("id")
                if not event_id:
                    continue
                url = f"{ODDS_BASE}/sports/{odds_sport}/events/{event_id}/odds"
                r = await client.get(
                    url,
                    params={
                        "apiKey": ODDS_API_KEY,
                        "regions": "us",
                        "markets": ",".join(allowed_markets),
                        "bookmakers": PROPS_BOOKMAKERS,
                        "oddsFormat": "american",
                        "dateFormat": "iso",
                    },
                )
                remaining = r.headers.get("X-Requests-Remaining")
                used = r.headers.get("X-Requests-Used")
                if remaining:
                    _quota_remaining = int(remaining)
                if used:
                    _quota_used_last = int(used)
                if r.status_code >= 400:
                    continue
                payload = r.json() if r.content else {}
                for bm in payload.get("bookmakers", []):
                    book = bm.get("title", "")
                    for market in bm.get("markets", []):
                        mkey = market.get("key", "")
                        for o in market.get("outcomes", []):
                            # For props, description typically holds player name.
                            player = (o.get("description") or o.get("name") or "").strip()
                            side = (o.get("name") or "").strip()
                            line = o.get("point")
                            odds = o.get("price")
                            if line is None or odds is None:
                                continue
                            rows.append(
                                {
                                    "sport": SPORT_META.get(sport_key, {}).get("label", sport_key),
                                    "market": mkey,
                                    "player": player,
                                    "side": side,
                                    "line": line,
                                    "odds": odds,
                                    "book": book,
                                    "game": f"{g.get('away_team','')} @ {g.get('home_team','')}",
                                    "game_time": g.get("commence_time"),
                                }
                            )
    except Exception:
        return []

    # Deduplicate by player/market/side/line/game with best price.
    best_map: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = f"{r.get('game')}|{r.get('player')}|{r.get('market')}|{r.get('side')}|{r.get('line')}"
        prev = best_map.get(key)
        if not prev or american_to_decimal(int(r.get("odds"))) > american_to_decimal(int(prev.get("odds"))):
            best_map[key] = r
    out = list(best_map.values())
    out.sort(key=lambda x: (x.get("game_time", ""), x.get("player", "")))
    out = out[:120]
    cache_set(cache_key, out)
    return out


def _pick_tracking_key(p: dict[str, Any]) -> str:
    return hashlib.md5(
        f"{p.get('sport')}|{p.get('game')}|{p.get('bet')}|{p.get('odds')}|{p.get('game_time')}".encode("utf-8")
    ).hexdigest()[:20]


def _american_profit_units(odds: Any) -> float:
    try:
        o = int(odds)
    except Exception:
        return 0.0
    if o > 0:
        return round(o / 100.0, 4)
    return round(100.0 / abs(o), 4)


def _extract_winner_from_score_row(row: dict[str, Any]) -> tuple[Optional[str], bool]:
    completed = bool(row.get("completed"))
    scores = row.get("scores") or []
    if not completed or not isinstance(scores, list) or len(scores) < 2:
        return None, completed
    best_team = None
    best_score = None
    tie = False
    for s in scores:
        team = s.get("name")
        val_raw = s.get("score")
        try:
            val = int(val_raw)
        except Exception:
            continue
        if best_score is None or val > best_score:
            best_score = val
            best_team = team
            tie = False
        elif val == best_score:
            tie = True
    if tie:
        return None, completed
    return best_team, completed


def _settle_user_tracked_picks(
    user_rec: dict[str, Any],
    scores_rows: list[dict[str, Any]],
    line_lookup: Optional[dict[str, dict[str, Any]]] = None,
) -> int:
    tracked = user_rec.setdefault("tracked_picks", [])
    if not tracked:
        return 0
    score_map = {}
    for row in scores_rows:
        if not isinstance(row, dict):
            continue
        game_key = f"{row.get('away_team','')} @ {row.get('home_team','')}"
        winner, completed = _extract_winner_from_score_row(row)
        score_map[game_key] = {"winner": winner, "completed": completed}
    updated = 0
    for tp in tracked:
        if tp.get("status") != "open":
            continue
        game = tp.get("game")
        outcome = score_map.get(game)
        if not outcome or not outcome.get("completed"):
            continue
        line = (line_lookup or {}).get(game) or {}
        side = str(tp.get("pick_side") or "")
        if side and line:
            closing_line = None
            if side == str(line.get("home_team")):
                closing_line = line.get("home_ml")
            elif side == str(line.get("away_team")):
                closing_line = line.get("away_ml")
            tp["closing_line"] = closing_line
        winner = outcome.get("winner")
        if not winner:
            tp["status"] = "push"
            tp["units"] = 0.0
            tp["settled_ts"] = int(time.time())
            updated += 1
            continue
        bet = str(tp.get("bet", ""))
        side = side or bet.replace(" ML", "").strip()
        if side and side == winner:
            tp["status"] = "win"
            tp["units"] = _american_profit_units(tp.get("odds"))
        else:
            tp["status"] = "loss"
            tp["units"] = -1.0
        tp["settled_ts"] = int(time.time())
        updated += 1
    if len(tracked) > 700:
        user_rec["tracked_picks"] = tracked[-700:]
    return updated


def _performance_summary(user_rec: dict[str, Any]) -> dict[str, Any]:
    rows = list(user_rec.get("tracked_picks", []))
    settled = [r for r in rows if r.get("status") in {"win", "loss", "push"}]
    wins = sum(1 for r in settled if r.get("status") == "win")
    losses = sum(1 for r in settled if r.get("status") == "loss")
    pushes = sum(1 for r in settled if r.get("status") == "push")
    graded = wins + losses + pushes
    units = round(sum(float(r.get("units", 0.0) or 0.0) for r in settled), 2)
    risked = max(1.0, float(sum(1.0 for r in settled if r.get("status") in {"win", "loss"})))
    win_pct = round((wins / max(1, wins + losses)) * 100.0, 1)
    roi_pct = round((units / risked) * 100.0, 1)
    month_rollup: dict[str, float] = {}
    for r in settled:
        ts = int(r.get("settled_ts") or r.get("ts") or 0)
        if ts <= 0:
            continue
        key = datetime.utcfromtimestamp(ts).strftime("%Y-%m")
        month_rollup[key] = month_rollup.get(key, 0.0) + float(r.get("units", 0.0) or 0.0)
    months = sorted(month_rollup.keys())[-6:]
    monthly = [{"month": m, "units": round(month_rollup[m], 2)} for m in months]
    avg_ev = round(
        sum(float(r.get("ev", 0.0) or 0.0) for r in settled) / max(1, len(settled)),
        2,
    )
    receipts = sorted(
        [
            {
                "ts": int(r.get("settled_ts") or r.get("ts") or 0),
                "game": r.get("game"),
                "bet": r.get("bet"),
                "odds": r.get("odds"),
                "line_at_pick": r.get("line_at_pick"),
                "closing_line": r.get("closing_line"),
                "status": r.get("status"),
                "units": round(float(r.get("units", 0.0) or 0.0), 2),
                "ev": r.get("ev"),
            }
            for r in settled
        ],
        key=lambda x: x.get("ts", 0),
        reverse=True,
    )[:40]
    return {
        "graded_picks": graded,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_pct": win_pct,
        "units": units,
        "roi_pct": roi_pct,
        "avg_ev": avg_ev,
        "open_picks": sum(1 for r in rows if r.get("status") == "open"),
        "monthly_units": monthly,
        "receipts": receipts,
    }


def _scan_streak_status(user_rec: dict[str, Any]) -> dict[str, Any]:
    history = list(user_rec.get("history", []))
    if not history:
        return {"current_days": 0, "best_days": 0, "next_reward_at": 5, "rewards_claimed": 0, "eligible_reward": False}
    days = sorted(
        set(datetime.utcfromtimestamp(int(h.get("ts", 0))).strftime("%Y-%m-%d") for h in history if int(h.get("ts", 0)) > 0),
        reverse=True,
    )
    if not days:
        return {"current_days": 0, "best_days": 0, "next_reward_at": 5, "rewards_claimed": 0, "eligible_reward": False}
    current = 1
    for i in range(1, len(days)):
        d_prev = datetime.strptime(days[i - 1], "%Y-%m-%d")
        d_cur = datetime.strptime(days[i], "%Y-%m-%d")
        if (d_prev - d_cur).days == 1:
            current += 1
        else:
            break
    best = 1
    run = 1
    for i in range(1, len(days)):
        d_prev = datetime.strptime(days[i - 1], "%Y-%m-%d")
        d_cur = datetime.strptime(days[i], "%Y-%m-%d")
        if (d_prev - d_cur).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1
    claimed = int(user_rec.get("streak_rewards_claimed", 0) or 0)
    next_reward_at = (claimed + 1) * 5
    return {
        "current_days": current,
        "best_days": best,
        "next_reward_at": next_reward_at,
        "rewards_claimed": claimed,
        "eligible_reward": current >= next_reward_at,
    }


def _clv_report(user_rec: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in user_rec.get("tracked_picks", []) if r.get("status") in {"win", "loss", "push"}]
    samples = []
    wins = 0
    for r in rows:
        try:
            open_line = int(r.get("line_at_pick"))
            close_line = int(r.get("closing_line"))
        except Exception:
            continue
        # Positive means user beat the closing line (better payout than close).
        delta = close_line - open_line
        samples.append(delta)
        if delta > 0:
            wins += 1
    count = len(samples)
    avg_delta = round(sum(samples) / count, 2) if count else 0.0
    beat_pct = round((wins / count) * 100.0, 1) if count else 0.0
    return {"samples": count, "avg_clv_delta": avg_delta, "beat_closing_pct": beat_pct}


def _agent_weighted_pick_score(pick: dict[str, Any], weights: dict[str, Any]) -> float:
    ev = float(pick.get("edge", 0.0) or 0.0)
    steam = 1.0 if float(pick.get("line_movement", 0.0) or 0.0) > 0 else 0.0
    fades = 1.0 if float(pick.get("public_pct", 0.0) or 0.0) >= 60 else 0.0
    return (
        ev * float(weights.get("best_line_ev", 1.0) or 1.0)
        + ev * 0.5 * float(weights.get("market_consensus", 1.0) or 1.0)
        + ev * 0.35 * float(weights.get("devig", 1.0) or 1.0)
        + steam * float(weights.get("steam", 1.0) or 1.0)
        + fades * float(weights.get("fades", 1.0) or 1.0)
    )


def _timeline_for_pick(entry: dict[str, Any]) -> list[dict[str, Any]]:
    ts = int(entry.get("ts", 0) or int(time.time()))
    ev = float(entry.get("ev", 0.0) or 0.0)
    line_at_pick = entry.get("line_at_pick")
    close = entry.get("closing_line")
    status = str(entry.get("status", "open"))
    out = [
        {"ts": ts, "event": "Pick published", "detail": f"{entry.get('bet')} {entry.get('odds')}"},
        {"ts": ts + 120, "event": "Market check", "detail": f"EV signal +{round(ev,2)}%"},
    ]
    if line_at_pick is not None:
        out.append({"ts": ts + 240, "event": "Line at pick", "detail": str(line_at_pick)})
    if close is not None:
        out.append({"ts": ts + 600, "event": "Closing line", "detail": str(close)})
    out.append({"ts": int(entry.get("settled_ts", ts + 900) or (ts + 900)), "event": "Settlement", "detail": status.upper()})
    return out


def _market_from_bet_text(bet: str, explicit: Optional[str] = None) -> str:
    if explicit:
        return str(explicit).strip().lower()
    b = str(bet or "").lower()
    if " ml" in b or b.endswith("ml"):
        return "moneyline"
    if "over" in b or "under" in b:
        return "total"
    if "+" in b or "-" in b:
        return "spread"
    return "other"


def _walk_forward_metrics_from_rows(rows: list[dict[str, Any]], weeks: int = 12) -> dict[str, Any]:
    now = datetime.utcnow()
    week_rows: list[dict[str, Any]] = []
    by_sport: dict[str, dict[str, Any]] = {}
    by_market: dict[str, dict[str, Any]] = {}
    for w in range(max(1, weeks)):
        start = now - timedelta(days=(w + 1) * 7)
        end = now - timedelta(days=w * 7)
        bucket = []
        for r in rows:
            ts = int(r.get("settled_ts") or r.get("ts") or 0)
            if ts <= 0:
                continue
            dt = datetime.utcfromtimestamp(ts)
            if dt >= start and dt < end:
                bucket.append(r)
        wins = sum(1 for r in bucket if r.get("status") == "win")
        losses = sum(1 for r in bucket if r.get("status") == "loss")
        graded = wins + losses + sum(1 for r in bucket if r.get("status") == "push")
        units = round(sum(float(r.get("units", 0.0) or 0.0) for r in bucket), 2)
        risked = max(1.0, float(wins + losses))
        win_pct = round((wins / max(1, wins + losses)) * 100.0, 1) if (wins + losses) else 0.0
        roi_pct = round((units / risked) * 100.0, 1) if (wins + losses) else 0.0
        clv_samples = []
        clv_wins = 0
        for r in bucket:
            try:
                open_line = int(r.get("line_at_pick"))
                close_line = int(r.get("closing_line"))
            except Exception:
                continue
            delta = close_line - open_line
            clv_samples.append(delta)
            if delta > 0:
                clv_wins += 1
        beat_closing = round((clv_wins / max(1, len(clv_samples))) * 100.0, 1) if clv_samples else 0.0
        week_rows.append(
            {
                "week_start": start.strftime("%Y-%m-%d"),
                "week_end": end.strftime("%Y-%m-%d"),
                "graded_picks": graded,
                "wins": wins,
                "losses": losses,
                "units": units,
                "win_pct": win_pct,
                "roi_pct": roi_pct,
                "beat_closing_pct": beat_closing,
            }
        )

    def _acc(stats: dict[str, Any], row: dict[str, Any]):
        stats["graded"] = int(stats.get("graded", 0)) + 1
        if row.get("status") == "win":
            stats["wins"] = int(stats.get("wins", 0)) + 1
        elif row.get("status") == "loss":
            stats["losses"] = int(stats.get("losses", 0)) + 1
        stats["units"] = float(stats.get("units", 0.0)) + float(row.get("units", 0.0) or 0.0)
        stats["ev_sum"] = float(stats.get("ev_sum", 0.0)) + float(row.get("ev", 0.0) or 0.0)
        stats["ev_n"] = int(stats.get("ev_n", 0)) + 1

    settled = [r for r in rows if r.get("status") in {"win", "loss", "push"}]
    for r in settled:
        sport = str(r.get("sport") or "unknown")
        market = _market_from_bet_text(r.get("bet"), r.get("bet_type"))
        _acc(by_sport.setdefault(sport, {}), r)
        _acc(by_market.setdefault(market, {}), r)

    def _finalize(group: dict[str, dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in group.items():
            wins = int(v.get("wins", 0))
            losses = int(v.get("losses", 0))
            risked = max(1.0, float(wins + losses))
            units = round(float(v.get("units", 0.0)), 2)
            out[k] = {
                "graded_picks": int(v.get("graded", 0)),
                "wins": wins,
                "losses": losses,
                "win_pct": round((wins / max(1, wins + losses)) * 100.0, 1) if (wins + losses) else 0.0,
                "units": units,
                "roi_pct": round((units / risked) * 100.0, 1) if (wins + losses) else 0.0,
                "avg_ev": round(float(v.get("ev_sum", 0.0)) / max(1, int(v.get("ev_n", 0))), 2),
            }
        return out

    return {
        "weeks": list(reversed(week_rows)),
        "by_sport": _finalize(by_sport),
        "by_market": _finalize(by_market),
    }


_BOOK_DEEPLINKS = {
    "draftkings": "https://sportsbook.draftkings.com",
    "fanduel": "https://sportsbook.fanduel.com",
    "betmgm": "https://sports.betmgm.com",
    "caesars": "https://sportsbook.caesars.com",
    "pinnacle": "https://www.pinnacle.com",
}

_ALERT_PACKS = {
    "a_grade_ev4": {"name": "A-grade +EV (4%+)", "min_ev": 4.0, "grade": "a"},
    "underdog_steam": {"name": "Underdog Steam", "min_ev": 2.0, "steam_only": True},
    "totals_only": {"name": "Totals Focus", "min_ev": 2.0, "bet_type": "totals"},
}


def _build_betslip_url(book: str, game: str, bet: str, odds: Any) -> str:
    base = _BOOK_DEEPLINKS.get(str(book or "").strip().lower())
    if not base:
        return ""
    q = urllib.parse.quote_plus(f"{game} {bet} {odds}")
    return f"{base}?q={q}"


def _fallback_picks_from_games(games: list[dict[str, Any]], max_count: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in games:
        home_odds = g.get("home_ml")
        away_odds = g.get("away_ml")
        if home_odds is None or away_odds is None:
            continue
        try:
            consensus_home, consensus_away, diag = market_consensus_fair_prob(g)
            home_ev = expected_value_pct(int(home_odds), float(consensus_home))
            away_ev = expected_value_pct(int(away_odds), float(consensus_away))
            if home_ev >= away_ev:
                side = g.get("home_team", "")
                odds = home_odds
                ev = float(home_ev)
                fair_prob = float(consensus_home)
                best_book = g.get("best_home_book")
            else:
                side = g.get("away_team", "")
                odds = away_odds
                ev = float(away_ev)
                fair_prob = float(consensus_away)
                best_book = g.get("best_away_book")
            # Keep near-fair fallback so UI still has useful picks when strict gates yield none.
            if ev < -0.5:
                continue
            implied_prob = calculate_implied_probability(int(odds)) * 100.0
            fair_pct = fair_prob * 100.0
            line_gap_pct = max(0.0, fair_pct - implied_prob)
            conf = _calibrated_confidence_pct(
                edge_ev=ev,
                books_count=int(diag.get("books_count", 1) or 1),
                disagreement_pct=float(diag.get("home_prob_spread", 0.0) or 0.0) * 100.0,
                line_gap_pct=line_gap_pct,
            )
            sport_key = str(g.get("sport") or g.get("sport_key") or "")
            meta = SPORT_META.get(sport_key, {"label": sport_key or "Game", "emoji": "🎯"})
            out.append(
                {
                    "id": f"fallback_{sport_key}_{g.get('home_team')}_{g.get('away_team')}_{time.time_ns()}",
                    "sport": sport_key,
                    "emoji": meta.get("emoji", "🎯"),
                    "label": meta.get("label", sport_key),
                    "home_team": g.get("home_team"),
                    "away_team": g.get("away_team"),
                    "game": f"{g.get('away_team','')} @ {g.get('home_team','')}",
                    "game_time": g.get("commence_time"),
                    "bet": f"{side} ML",
                    "bet_type": "moneyline",
                    "odds": odds,
                    "edge": round(ev, 2),
                    "ev": round(ev, 2),
                    "confidence": int(round(conf)),
                    "confidence_calibrated": round(conf, 1),
                    "fair_prob": round(fair_pct, 1),
                    "implied_prob": round(implied_prob, 1),
                    "best_book": best_book or "DraftKings",
                    "books_compared": int(diag.get("books_count", 1) or 1),
                    "market_disagreement": round(float(diag.get("home_prob_spread", 0.0) or 0.0) * 100.0, 2),
                    "clv_expectation": round(line_gap_pct, 2),
                    "model_breakdown": {
                        "pinnacle_clv": f"Fallback mode: showing best available edges while strict filters are quiet.",
                        "sharp_money": f"{int(diag.get('books_count', 1) or 1)} books compared.",
                        "confirms": "Fallback ranking by EV + calibrated confidence",
                    },
                    "agents_fired": ["fallback_ranker"],
                    "data_source": "odds_api",
                }
            )
        except Exception:
            continue
    out.sort(key=lambda x: (float(x.get("edge", 0.0)), float(x.get("confidence", 0.0))), reverse=True)
    return out[: max(1, int(max_count or 12))]


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


def _calibrated_confidence_pct(edge_ev: float, books_count: int, disagreement_pct: float, line_gap_pct: float) -> float:
    # Logistic-style calibration: penalize noisy markets and low-book coverage.
    z = (
        -1.25
        + 0.34 * float(edge_ev or 0.0)
        + 0.10 * max(0, int(books_count or 1) - 1)
        - 0.07 * max(0.0, float(disagreement_pct or 0.0))
        + 0.05 * max(0.0, float(line_gap_pct or 0.0))
    )
    p = 1.0 / (1.0 + math.exp(-z))
    return max(50.0, min(95.0, p * 100.0))


def _build_model_v2_components(edge_ev: float, books_count: int, disagreement_pct: float, line_gap_pct: float) -> dict[str, float]:
    # Component scores are intentionally simple and bounded; they can later be replaced by learned models.
    market_score = max(0.0, min(100.0, 52.0 + edge_ev * 4.2))
    liquidity_score = max(0.0, min(100.0, 35.0 + books_count * 8.0))
    stability_score = max(0.0, min(100.0, 88.0 - disagreement_pct * 2.2))
    timing_score = max(0.0, min(100.0, 50.0 + line_gap_pct * 4.0))
    ensemble = round(
        market_score * 0.44 + liquidity_score * 0.22 + stability_score * 0.22 + timing_score * 0.12,
        2,
    )
    return {
        "market_model": round(market_score, 2),
        "liquidity_model": round(liquidity_score, 2),
        "stability_model": round(stability_score, 2),
        "timing_model": round(timing_score, 2),
        "ensemble_score": ensemble,
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

        if max(home_ev, away_ev) < MODEL_MIN_EDGE_EV:
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

        disagreement = float(diag.get("home_prob_spread", 0.0) or 0.0)
        books_count = int(diag.get("books_count", 1) or 1)
        implied_prob_pct = calculate_implied_probability(bet_odds) * 100.0
        fair_pct = fair_prob * 100.0
        line_gap_pct = max(0.0, fair_pct - implied_prob_pct)
        confidence_raw = _calibrated_confidence_pct(
            edge_ev=float(edge or 0.0),
            books_count=books_count,
            disagreement_pct=disagreement * 100.0,
            line_gap_pct=line_gap_pct,
        )
        confidence = confidence_raw
        if books_count < MODEL_MIN_BOOKS:
            continue
        if confidence < MODEL_MIN_CONFIDENCE:
            continue
        model_v2 = _build_model_v2_components(
            edge_ev=float(edge or 0.0),
            books_count=books_count,
            disagreement_pct=disagreement * 100.0,
            line_gap_pct=line_gap_pct,
        )

        game_time = game.get("commence_time", "")
        implied_pct = implied_prob_pct

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
            "confidence_calibrated": round(confidence_raw, 1),
            "fair_prob": round(fair_pct, 1),
            "implied_prob": round(implied_pct, 1),
            "best_book": best_book or (game.get("bookmakers", ["DraftKings"])[0] if game.get("bookmakers") else "DraftKings"),
            "books_compared": books_count,
            "market_disagreement": round(disagreement * 100.0, 2),
            "clv_expectation": round(line_gap_pct, 2),
            "model_v2": model_v2,
            "model_breakdown": {
                "pinnacle_clv": f"Consensus fair {fair_pct:.1f}% vs implied {implied_pct:.1f}% ({edge:+.2f}% EV).",
                "sharp_money": f"Compared across {books_count} books; disagreement {disagreement*100.0:.2f} pts.",
                "confirms": "Best-line EV, de-vig consensus, book quality weighting, calibrated confidence",
            },
            "agents_fired": ["best_line_ev", "market_consensus", "devig", "confidence_calibration"],
            "data_source": "odds_api",
        }
        picks.append(pick)

    picks.sort(key=lambda x: (float(x.get("model_v2", {}).get("ensemble_score", 0.0)), float(x.get("edge", 0.0))), reverse=True)
    return picks[: max(1, MODEL_MAX_PICKS_PER_SPORT)]


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
        "features": ["50 picks per scan", "All sports", "+EV finder", "Steam feed", "Refresh every minute"],
    },
}


class CheckoutRequest(BaseModel):
    tier: str
    billing_cycle: Optional[str] = "monthly"
    success_url: str
    cancel_url: str
    user_id: Optional[str] = None


class PortalRequest(BaseModel):
    return_url: str
    user_id: Optional[str] = None


class AuthSessionRequest(BaseModel):
    user_id: str
    method: Optional[str] = "guest"
    identifier: Optional[str] = ""


class AuthGoogleRequest(BaseModel):
    id_token: str


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


class DigestSubscribeRequest(BaseModel):
    enabled: Optional[bool] = True
    channel: Optional[str] = "email"
    target: Optional[str] = ""
    hour_local: Optional[int] = 9


class WaitlistJoinRequest(BaseModel):
    email: str
    source: Optional[str] = "props_cap"


class ProfileSettingsRequest(BaseModel):
    state: Optional[str] = "auto"
    bankroll_mode: Optional[str] = "standard"


class BankrollPlanRequest(BaseModel):
    bankroll: float
    risk_mode: Optional[str] = "standard"
    max_open_bets: Optional[int] = 8


class JournalEntryRequest(BaseModel):
    game: str
    bet: str
    odds: str
    stake_units: Optional[float] = 1.0
    result: Optional[str] = "open"
    notes: Optional[str] = ""


class AgentWeightsRequest(BaseModel):
    best_line_ev: Optional[float] = 1.0
    market_consensus: Optional[float] = 1.0
    devig: Optional[float] = 1.0
    steam: Optional[float] = 1.0
    fades: Optional[float] = 1.0


class AlertPackRequest(BaseModel):
    pack: str
    enabled: Optional[bool] = True


class TelemetryEventRequest(BaseModel):
    name: str
    props: Optional[dict[str, Any]] = None


COMMUNITY_POST_WINDOW_SECONDS = 10 * 60
COMMUNITY_POST_MAX_PER_WINDOW = 3
COMMUNITY_POST_MAX_PER_DAY = 15


def _request_user_id(request: Request) -> str:
    state_uid = _normalize_user_id(str(getattr(request.state, "user_id", "") or ""))
    if state_uid:
        return state_uid
    return _normalize_user_id(request.headers.get("x-user-id", ""))


def _require_admin(request: Request):
    token = str(request.headers.get("x-admin-token", "")).strip()
    if not ADMIN_API_TOKEN or token != ADMIN_API_TOKEN:
        raise HTTPException(status_code=401, detail="Admin token required.")


def _require_debug_access(request: Request):
    if DEBUG_ENDPOINTS_PUBLIC:
        return
    _require_admin(request)


def _is_allowed_billing_return_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in {"https", "http"}:
        return False
    if not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/").lower()
    if origin in BILLING_RETURN_ORIGINS:
        return True
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        return True
    return False


def _plan_from_price_id(price_id: str) -> str:
    p = str(price_id or "").strip()
    if not p:
        return PLAN_FREE
    if p in VIP_PRICE_IDS or p in VIP_ANNUAL_PRICE_IDS:
        return PLAN_VIP
    if p in PREMIUM_PRICE_IDS or p in PREMIUM_ANNUAL_PRICE_IDS:
        return PLAN_PREMIUM
    return PLAN_PREMIUM


def _resolve_price_id_for_tier_and_cycle(tier: str, billing_cycle: Optional[str] = "monthly") -> str:
    t = normalize_plan_name(tier)
    cycle = str(billing_cycle or "monthly").strip().lower()
    is_annual = cycle in ("annual", "yearly", "year")
    if t == PLAN_PREMIUM:
        if is_annual and PREMIUM_ANNUAL_PRICE_IDS:
            return PREMIUM_ANNUAL_PRICE_IDS[0]
        if not PREMIUM_PRICE_IDS:
            raise HTTPException(status_code=500, detail="Premium price is not configured.")
        return PREMIUM_PRICE_IDS[0]
    if t == PLAN_VIP:
        if is_annual and VIP_ANNUAL_PRICE_IDS:
            return VIP_ANNUAL_PRICE_IDS[0]
        if not VIP_PRICE_IDS:
            raise HTTPException(status_code=500, detail="VIP price is not configured.")
        return VIP_PRICE_IDS[0]
    raise HTTPException(status_code=400, detail="Free tier does not require checkout.")


def _billing_customer_email_for_user(user_id: str) -> str:
    rec = _ensure_growth_user(user_id)
    identity = rec.get("profile_identity", {}) if isinstance(rec, dict) else {}
    identifier = str((identity or {}).get("identifier", "")).strip().lower()
    if re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", identifier):
        return identifier
    return f"{user_id}@users.algobets.local"


def _find_or_create_customer(user_id: str):
    email = _billing_customer_email_for_user(user_id)
    try:
        customers = stripe.Customer.search(query=f'metadata["userId"]:"{user_id}"', limit=1)
        if customers.data:
            return customers.data[0]
    except Exception:
        pass
    return stripe.Customer.create(email=email, metadata={"userId": user_id})


def _invalidate_plan_cache(user_id: str):
    if not user_id:
        return
    _plan_cache.pop(user_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/debug")
async def debug(request: Request):
    """Debug endpoint to check configuration."""
    _require_debug_access(request)
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


@app.get("/api/config/public")
async def public_config():
    return {
        "vip_discord_url": VIP_DISCORD_URL,
        "google_client_id": GOOGLE_CLIENT_ID,
        "billing_enabled": BILLING_ENABLED,
        "auth_required": REQUIRE_AUTH_TOKEN,
    }


@app.post("/api/auth/session")
@limiter.limit("60/hour")
async def auth_session(body: AuthSessionRequest, request: Request):
    user_id = _normalize_user_id(body.user_id)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid user_id.")
    method = str(body.method or "guest").strip().lower()[:24]
    identifier = str(body.identifier or "").strip().lower()[:160]
    rec = _ensure_growth_user(user_id)
    profile = rec.setdefault("profile_identity", {})
    profile.update({"method": method or "guest", "identifier": identifier, "updated_at": int(time.time())})
    _save_growth_db()
    token = _issue_hs256_jwt(user_id)
    return {"token": token, "user_id": user_id, "expires_in": AUTH_TOKEN_TTL_SECONDS}


@app.post("/api/auth/google")
@limiter.limit("90/hour")
async def auth_google(body: AuthGoogleRequest, request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google auth is not configured.")
    raw_token = str(body.id_token or "").strip()
    if not raw_token:
        raise HTTPException(status_code=400, detail="id_token is required.")
    try:
        claims = google_id_token.verify_oauth2_token(raw_token, google_requests.Request(), GOOGLE_CLIENT_ID)
    except Exception as e:
        _audit_security_event(request, "auth.google_invalid_token", str(e))
        raise HTTPException(status_code=401, detail="Invalid Google token.")

    sub = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    email_verified = bool(claims.get("email_verified"))
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid Google identity.")
    if not email or not email_verified:
        raise HTTPException(status_code=401, detail="Google account email must be verified.")

    user_id = _normalize_user_id(f"g_{sub}")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Google identity.")

    rec = _ensure_growth_user(user_id)
    profile = rec.setdefault("profile_identity", {})
    profile.update({"method": "google", "identifier": email, "updated_at": int(time.time())})
    _save_growth_db()
    token = _issue_hs256_jwt(user_id)
    return {
        "token": token,
        "user_id": user_id,
        "expires_in": AUTH_TOKEN_TTL_SECONDS,
        "profile": {"method": "google", "identifier": email},
    }


@app.get("/api/legal/terms")
async def legal_terms():
    return {
        "title": "Algobets Terms (Summary)",
        "updated_at": "2026-03-06",
        "sections": [
            "Informational use only. Not financial advice.",
            "User responsible for legal compliance in their jurisdiction.",
            "Subscriptions auto-renew until canceled via billing portal.",
            "Abuse, scraping, or fraud may result in account termination.",
        ],
    }


@app.get("/api/legal/privacy")
async def legal_privacy():
    return {
        "title": "Algobets Privacy (Summary)",
        "updated_at": "2026-03-06",
        "sections": [
            "We store account identifiers, subscription status, and feature usage metadata.",
            "We store community and alert content submitted by users.",
            "Payment details are processed by Stripe and not stored directly by this app.",
            "You may request account data deletion via /api/privacy/delete-request.",
        ],
    }


@app.post("/api/privacy/delete-request")
@limiter.limit("5/day")
async def privacy_delete_request(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="Authenticated user id is required.")
    pending = _growth_db.setdefault("privacy_delete_requests", [])
    if not any(str(x.get("user_id", "")) == user_id for x in pending if isinstance(x, dict)):
        pending.append({"user_id": user_id, "ts": int(time.time()), "status": "pending"})
        _growth_db["privacy_delete_requests"] = pending[-2000:]
        _save_growth_db()
    _audit_security_event(request, "privacy.delete_request", user_id=user_id)
    return {"ok": True, "status": "pending"}


@app.post("/api/telemetry/event")
@limiter.limit("240/minute")
async def telemetry_event(body: TelemetryEventRequest, request: Request):
    name = str(body.name or "").strip().lower()[:64]
    if not re.match(r"^[a-z0-9_\\.-]{2,64}$", name):
        raise HTTPException(status_code=400, detail="Invalid event name.")
    props = body.props if isinstance(body.props, dict) else {}
    user_id = _request_user_id(request) or "guest"
    plan = await _get_verified_plan(request)
    events = _growth_db.setdefault("telemetry_events", [])
    events.append(
        {
            "ts": int(time.time()),
            "name": name,
            "user_id": str(user_id)[:120],
            "plan": normalize_plan_name(plan),
            "ip": _client_ip(request)[:80],
            "props": {str(k)[:40]: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v) for k, v in props.items()},
        }
    )
    _growth_db["telemetry_events"] = events[-50000:]
    _save_growth_db()
    return {"ok": True}


@app.get("/api/admin/telemetry/funnel")
async def admin_telemetry_funnel(request: Request, days: int = 30):
    _require_admin(request)
    window_days = max(1, min(int(days or 30), 90))
    cutoff = int(time.time()) - window_days * 86400
    rows = [e for e in _growth_db.get("telemetry_events", []) if int(e.get("ts", 0) or 0) >= cutoff]
    steps = ["app_open", "scan_manual", "auth_nudge_shown", "auth_completed", "checkout_started", "checkout_redirected", "checkout_success"]
    totals = {s: 0 for s in steps}
    users_by_step: dict[str, set[str]] = {s: set() for s in steps}
    for e in rows:
        n = str(e.get("name", ""))
        if n in totals:
            totals[n] += 1
            uid = str(e.get("user_id", "")).strip()
            if uid:
                users_by_step[n].add(uid)
    unique = {k: len(v) for k, v in users_by_step.items()}
    base = max(1, unique.get("app_open", 0))
    conversion = {k: round((unique.get(k, 0) / base) * 100.0, 1) for k in steps}
    return {"days": window_days, "events": totals, "unique_users": unique, "conversion_pct_from_open": conversion}


@app.get("/api/admin/security/events")
async def admin_security_events(request: Request, limit: int = 200):
    _require_admin(request)
    rows = list(_growth_db.get("security_events", []))
    rows.sort(key=lambda x: x.get("ts", 0), reverse=True)
    safe_limit = max(1, min(int(limit or 200), 1000))
    return {"events": rows[:safe_limit], "count": len(rows)}


@app.get("/api/admin/backup/export")
async def admin_backup_export(request: Request):
    _require_admin(request)
    payload = {
        "exported_at": int(time.time()),
        "growth_db": _growth_db,
    }
    _audit_security_event(request, "admin.backup_export")
    return payload


@app.post("/api/admin/backup/restore")
@limiter.limit("2/hour")
async def admin_backup_restore(request: Request):
    _require_admin(request)
    restore_enabled = _bool_env("ADMIN_RESTORE_ENABLED", "false")
    if not restore_enabled:
        raise HTTPException(status_code=403, detail="Restore is disabled.")
    body = await request.json()
    incoming = body.get("growth_db")
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="growth_db object is required.")
    global _growth_db
    _growth_db = incoming
    _save_growth_db()
    _audit_security_event(request, "admin.backup_restore")
    return {"ok": True}


@app.get("/api/pricing")
async def pricing():
    return {
        "tiers": {
            PLAN_FREE: {
                **TIER_CONFIG[PLAN_FREE],
                "billing": {"monthly_configured": False, "annual_configured": False},
            },
            PLAN_PREMIUM: {
                **TIER_CONFIG[PLAN_PREMIUM],
                "price_ids_configured": len(PREMIUM_PRICE_IDS) > 0,
                "billing": {
                    "monthly_configured": len(PREMIUM_PRICE_IDS) > 0,
                    "annual_configured": len(PREMIUM_ANNUAL_PRICE_IDS) > 0,
                },
            },
            PLAN_VIP: {
                **TIER_CONFIG[PLAN_VIP],
                "price_ids_configured": len(VIP_PRICE_IDS) > 0,
                "billing": {
                    "monthly_configured": len(VIP_PRICE_IDS) > 0,
                    "annual_configured": len(VIP_ANNUAL_PRICE_IDS) > 0,
                },
            },
        }
    }


@app.get("/api/plan")
async def current_plan(plan: str = Depends(get_user_plan)):
    normalized = normalize_plan_name(plan)
    return {
        "plan": normalized,
        "tier": TIER_CONFIG.get(normalized, TIER_CONFIG[PLAN_FREE]),
    }


@app.get("/api/model/report")
async def model_report():
    return {
        "model": {
            "name": "algobets_ensemble_v2",
            "objective": "maximize_ev_and_clv",
            "components": ["market_model", "liquidity_model", "stability_model", "timing_model"],
            "selection_gates": {
                "min_edge_ev": MODEL_MIN_EDGE_EV,
                "min_books": MODEL_MIN_BOOKS,
                "min_confidence": MODEL_MIN_CONFIDENCE,
                "max_picks_per_sport": MODEL_MAX_PICKS_PER_SPORT,
            },
            "notes": "Calibrated confidence with liquidity and disagreement penalties.",
        }
    }


@app.get("/api/model/walkforward")
async def model_walkforward(request: Request, weeks: int = 12, scope: str = "user"):
    window_weeks = max(2, min(int(weeks or 12), 52))
    sc = str(scope or "user").strip().lower()
    if sc == "global":
        _require_admin(request)
        rows: list[dict[str, Any]] = []
        users = _growth_db.get("users", {})
        if isinstance(users, dict):
            for _, rec in users.items():
                if not isinstance(rec, dict):
                    continue
                rows.extend([r for r in rec.get("tracked_picks", []) if isinstance(r, dict)])
        metrics = _walk_forward_metrics_from_rows(rows, weeks=window_weeks)
        return {"scope": "global", "weeks_requested": window_weeks, "total_rows": len(rows), "metrics": metrics}
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    rows = [r for r in rec.get("tracked_picks", []) if isinstance(r, dict)]
    metrics = _walk_forward_metrics_from_rows(rows, weeks=window_weeks)
    return {"scope": "user", "user_id": user_id, "weeks_requested": window_weeks, "total_rows": len(rows), "metrics": metrics}


@app.get("/api/referral/status")
async def referral_status(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    _ensure_growth_user(user_id)
    _save_growth_db()
    return _build_referral_status(user_id)


@app.post("/api/referral/redeem")
@limiter.limit("20/day")
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

    # Milestone reward: every 2 successful invites grants referrer +7 days Premium.
    referrals_count = len(owner_rec.get("referrals", []))
    earned_pairs = referrals_count // 2
    bonus_pairs_awarded = int(owner_rec.get("referral_bonus_pairs_awarded", 0) or 0)
    if earned_pairs > bonus_pairs_awarded:
        bonus_seconds = (earned_pairs - bonus_pairs_awarded) * (7 * 24 * 3600)
        owner_rec["trial_until"] = max(
            float(owner_rec.get("trial_until", 0) or 0),
            now,
        ) + bonus_seconds
        owner_rec["referral_bonus_pairs_awarded"] = earned_pairs

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


@app.get("/api/trial/status")
async def trial_status(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    now = time.time()
    trial_until = float(rec.get("trial_until", 0) or 0)
    return {
        "eligible": not bool(rec.get("free_trial_claimed")),
        "claimed": bool(rec.get("free_trial_claimed")),
        "claimed_at": int(rec.get("free_trial_claimed_at", 0) or 0) or None,
        "trial_active_until": int(trial_until) if trial_until > now else None,
        "trial_seconds_left": max(0, int(trial_until - now)),
    }


@app.get("/api/profile/settings")
async def profile_settings_get(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    p = rec.get("profile", {}) if isinstance(rec.get("profile"), dict) else {}
    return {
        "settings": {
            "state": str(p.get("state", "auto")),
            "bankroll_mode": str(p.get("bankroll_mode", "standard")),
        }
    }


@app.post("/api/profile/settings")
@limiter.limit("60/hour")
async def profile_settings_set(body: ProfileSettingsRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    state = str(body.state or "auto").strip().upper()
    if state == "AUTO":
        state = "auto"
    if state != "auto" and (len(state) != 2 or not state.isalpha()):
        raise HTTPException(status_code=400, detail="state must be 'auto' or 2-letter code.")
    mode = str(body.bankroll_mode or "standard").strip().lower()
    if mode not in {"conservative", "standard", "aggressive"}:
        raise HTTPException(status_code=400, detail="bankroll_mode must be conservative/standard/aggressive.")
    rec["profile"] = {"state": state, "bankroll_mode": mode}
    _save_growth_db()
    return {"ok": True, "settings": rec["profile"]}


@app.post("/api/trial/start")
@limiter.limit("5/day")
async def trial_start(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    ip = _client_ip(request)
    allow_user, _, retry_user = _velocity_allow("trial_user", user_id.lower(), TRIAL_MAX_PER_DAY, 86400)
    allow_ip, _, retry_ip = _velocity_allow("trial_ip", ip, max(2, TRIAL_MAX_PER_DAY * 4), 86400)
    if not allow_user or not allow_ip:
        retry_after = max(retry_user, retry_ip)
        _audit_security_event(request, "fraud.trial_velocity_block", f"user={user_id} retry={retry_after}", user_id=user_id)
        raise HTTPException(status_code=429, detail=f"Too many trial attempts. Retry in {retry_after}s.")

    # Paid users do not need free trial grants.
    stripe_plan = await asyncio.to_thread(_verify_plan_stripe_sync, user_id)
    if plan_rank(stripe_plan) >= plan_rank(PLAN_PREMIUM):
        raise HTTPException(status_code=400, detail="Paid plan already active.")

    rec = _ensure_growth_user(user_id)
    if rec.get("free_trial_claimed"):
        raise HTTPException(status_code=400, detail="Free trial already claimed.")

    now = time.time()
    grant_seconds = 72 * 3600
    rec["free_trial_claimed"] = True
    rec["free_trial_claimed_at"] = int(now)
    rec["trial_until"] = max(float(rec.get("trial_until", 0) or 0), now + grant_seconds)
    _save_growth_db()
    _invalidate_plan_cache(user_id)
    return {
        "ok": True,
        "granted_hours": 72,
        "trial_plan": PLAN_PREMIUM,
        "trial_until": int(rec["trial_until"]),
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
@limiter.limit("30/hour")
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
    if not COMMUNITY_ENABLED:
        raise HTTPException(status_code=503, detail="Community is temporarily disabled.")
    posts = list(_growth_db.get("community_posts", []))
    posts.sort(key=lambda x: x.get("ts", 0), reverse=True)
    safe_limit = max(1, min(int(limit or 40), 100))
    return {"posts": posts[:safe_limit], "count": len(posts)}


@app.post("/api/community/posts")
@limiter.limit("40/hour")
async def community_create_post(body: CommunityPostRequest, request: Request):
    if not COMMUNITY_ENABLED:
        raise HTTPException(status_code=503, detail="Community is temporarily disabled.")
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    if not _is_registered_user_id(user_id):
        raise HTTPException(status_code=403, detail="Sign in is required to post.")

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

    # Verified feed only: require actual pick details to prevent spam/freeform dumping.
    if not all([bet, game, odds]):
        raise HTTPException(status_code=400, detail="Verified pick fields (bet, game, odds) are required.")
    if re.search(r"https?://|www\.", text, flags=re.IGNORECASE) or re.search(r"https?://|www\.", bet, flags=re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Links are not allowed in community posts.")
    if not re.match(r"^[+-]?\d{2,4}$", odds):
        raise HTTPException(status_code=400, detail="Invalid odds format.")

    rec = _ensure_growth_user(user_id)
    now_ts = int(time.time())
    post_times = [int(ts) for ts in rec.get("community_post_timestamps", []) if int(ts) > (now_ts - 86400)]
    window_count = len([ts for ts in post_times if ts > (now_ts - COMMUNITY_POST_WINDOW_SECONDS)])
    if window_count >= COMMUNITY_POST_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="Too many posts. Please wait a few minutes.")
    if len(post_times) >= COMMUNITY_POST_MAX_PER_DAY:
        raise HTTPException(status_code=429, detail="Daily post limit reached. Try again tomorrow.")
    rec["community_post_timestamps"] = post_times + [now_ts]

    if not text:
        text = "Cashed this pick." if mode == "win" else "Sharing this pick."

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
        "verified": True,
    }
    feed = _growth_db.setdefault("community_posts", [])
    feed.append(post)
    _growth_db["community_posts"] = feed[-500:]
    _save_growth_db()
    return {"ok": True, "post": post}


@app.get("/api/community/leaderboard")
async def community_leaderboard(limit: int = 20):
    if not COMMUNITY_ENABLED:
        raise HTTPException(status_code=503, detail="Community is temporarily disabled.")
    posts = list(_growth_db.get("community_posts", []))
    posts.sort(key=lambda x: x.get("ts", 0), reverse=True)
    scores: dict[str, dict[str, Any]] = {}
    daily_caps: dict[str, int] = {}
    seen_user_game_day: set[str] = set()
    for p in posts:
        if not p.get("verified"):
            continue
        user = str(p.get("user") or "anon")
        mode = str(p.get("mode") or "pick")
        game = str(p.get("game") or "")
        day = datetime.utcfromtimestamp(int(p.get("ts", 0) or 0)).strftime("%Y-%m-%d")
        dedupe_key = f"{user}|{game}|{day}|{mode}"
        if dedupe_key in seen_user_game_day:
            continue
        seen_user_game_day.add(dedupe_key)
        cap_key = f"{user}|{day}|{mode}"
        daily_caps.setdefault(cap_key, 0)
        if daily_caps[cap_key] >= 3:
            continue
        daily_caps[cap_key] += 1
        row = scores.setdefault(user, {"user": user, "wins": 0, "picks": 0, "score": 0.0, "ev_sum": 0.0, "ev_count": 0})
        if mode == "win":
            row["wins"] += 1
            row["score"] += 2.0
        else:
            row["picks"] += 1
            row["score"] += 1.0
        if p.get("ev") is not None:
            try:
                row["ev_sum"] += float(p.get("ev"))
                row["ev_count"] += 1
            except Exception:
                pass
    rows = []
    for r in scores.values():
        avg_ev = (r["ev_sum"] / r["ev_count"]) if r["ev_count"] > 0 else 0.0
        weighted = r["score"] + max(0.0, avg_ev) * 0.08
        rows.append(
            {
                "user": r["user"],
                "wins": r["wins"],
                "picks": r["picks"],
                "avg_ev": round(avg_ev, 2),
                "score": round(weighted, 2),
            }
        )
    rows.sort(key=lambda x: (x.get("score", 0.0), x.get("wins", 0)), reverse=True)
    safe_limit = max(1, min(int(limit or 20), 50))
    return {"leaders": rows[:safe_limit], "count": len(rows)}


@app.get("/api/digest/subscription")
async def digest_subscription_get(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    return {"digest": rec.get("digest", {"enabled": False, "channel": "email", "target": "", "hour_local": 9})}


@app.post("/api/digest/subscription")
@limiter.limit("30/hour")
async def digest_subscription_set(body: DigestSubscribeRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    if not _is_registered_user_id(user_id):
        raise HTTPException(status_code=403, detail="Sign in is required.")
    channel = (body.channel or "email").strip().lower()
    if channel not in {"email", "sms"}:
        raise HTTPException(status_code=400, detail="channel must be 'email' or 'sms'.")
    target = (body.target or "").strip()
    hour_local = max(0, min(int(body.hour_local if body.hour_local is not None else 9), 23))
    rec = _ensure_growth_user(user_id)
    rec["digest"] = {
        "enabled": bool(body.enabled),
        "channel": channel,
        "target": target,
        "hour_local": hour_local,
        "updated_at": int(time.time()),
    }
    _save_growth_db()
    return {"ok": True, "digest": rec["digest"]}


@app.get("/api/digest/preview")
async def digest_preview(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    history = list(rec.get("history", []))
    history.sort(key=lambda x: x.get("ts", 0), reverse=True)
    top: list[dict[str, Any]] = []
    if history:
        top = list(history[0].get("top_picks", []))[:3]
    streak = _scan_streak_status(rec)
    now = datetime.now()
    slots = [9, 13, 18]
    next_slot = None
    for h in slots:
        if now.hour < h:
            next_slot = h
            break
    if next_slot is None:
        next_slot = slots[0]
    return {
        "preview": {
            "headline": "Today’s Top Edges",
            "top_picks": top,
            "streak": streak.get("current_days", 0),
            "send_slots_local": slots,
            "next_slot_local_hour": next_slot,
        }
    }


@app.get("/api/streak/status")
async def streak_status(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    return {"streak": _scan_streak_status(rec)}


@app.post("/api/streak/claim")
@limiter.limit("15/day")
async def streak_claim(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    streak = _scan_streak_status(rec)
    if not streak.get("eligible_reward"):
        raise HTTPException(status_code=400, detail="No streak reward available yet.")
    now = time.time()
    rec["streak_rewards_claimed"] = int(rec.get("streak_rewards_claimed", 0) or 0) + 1
    # Reward: +24h Premium trial extension.
    rec["trial_until"] = max(float(rec.get("trial_until", 0) or 0), now) + (24 * 3600)
    _save_growth_db()
    _invalidate_plan_cache(user_id)
    return {"ok": True, "granted_hours": 24, "streak": _scan_streak_status(rec)}


@app.post("/api/waitlist/join")
@limiter.limit("20/hour")
async def waitlist_join(body: WaitlistJoinRequest, request: Request):
    ip = _client_ip(request)
    allow_waitlist, _, retry_after = _velocity_allow("waitlist_ip", ip, WAITLIST_MAX_PER_HOUR, 3600)
    if not allow_waitlist:
        _audit_security_event(request, "fraud.waitlist_rate_limited", f"retry_after={retry_after}")
        raise HTTPException(status_code=429, detail=f"Too many waitlist requests. Retry in {retry_after}s.")
    email = (body.email or "").strip().lower()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=400, detail="Valid email is required.")
    source = (body.source or "props_cap").strip()[:48]
    waitlist = _growth_db.setdefault("waitlist", [])
    if not any(str(x.get("email", "")).lower() == email for x in waitlist if isinstance(x, dict)):
        waitlist.append({"email": email, "source": source, "ts": int(time.time())})
        _growth_db["waitlist"] = waitlist[-5000:]
        _save_growth_db()
    user_id = _request_user_id(request)
    if user_id:
        rec = _ensure_growth_user(user_id)
        rec.setdefault("waitlist", []).append({"email": email, "source": source, "ts": int(time.time())})
        rec["waitlist"] = rec["waitlist"][-50:]
        _save_growth_db()
    return {"ok": True}


@app.post("/api/billing/checkout")
@limiter.limit("20/hour")
async def billing_checkout(body: CheckoutRequest, request: Request):
    if not BILLING_ENABLED:
        raise HTTPException(status_code=503, detail="Billing is temporarily disabled.")
    if not STRIPE_SECRET:
        raise HTTPException(status_code=500, detail="Stripe is not configured.")
    user_id = _normalize_user_id(_request_user_id(request))
    if not user_id:
        raise HTTPException(status_code=400, detail="Authenticated user required.")
    if not _is_allowed_billing_return_url(body.success_url) or not _is_allowed_billing_return_url(body.cancel_url):
        raise HTTPException(status_code=400, detail="Invalid checkout redirect URL.")
    ip = _client_ip(request)
    allow_user, _, retry_user = _velocity_allow("checkout_user", user_id.lower(), CHECKOUT_MAX_PER_HOUR, 3600)
    allow_ip, _, retry_ip = _velocity_allow("checkout_ip", ip, CHECKOUT_MAX_PER_HOUR * 2, 3600)
    if not allow_user or not allow_ip:
        retry_after = max(retry_user, retry_ip)
        _audit_security_event(request, "fraud.checkout_velocity_block", f"user={user_id} retry={retry_after}", user_id=user_id)
        raise HTTPException(status_code=429, detail=f"Too many checkout attempts. Retry in {retry_after}s.")

    tier = normalize_plan_name(body.tier)
    billing_cycle = str(body.billing_cycle or "monthly").strip().lower()
    price_id = _resolve_price_id_for_tier_and_cycle(tier, billing_cycle)
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
        metadata={"userId": user_id, "tier": tier, "billing_cycle": billing_cycle},
    )
    return {"checkout_url": session.url, "session_id": session.id}


@app.post("/api/billing/portal")
@limiter.limit("20/hour")
async def billing_portal(body: PortalRequest, request: Request):
    if not BILLING_ENABLED:
        raise HTTPException(status_code=503, detail="Billing is temporarily disabled.")
    if not STRIPE_SECRET:
        raise HTTPException(status_code=500, detail="Stripe is not configured.")
    user_id = _normalize_user_id(_request_user_id(request))
    if not user_id:
        raise HTTPException(status_code=400, detail="Authenticated user required.")
    if not _is_allowed_billing_return_url(body.return_url):
        raise HTTPException(status_code=400, detail="Invalid portal return URL.")

    customer = await asyncio.to_thread(_find_or_create_customer, user_id)
    session = await asyncio.to_thread(
        stripe.billing_portal.Session.create,
        customer=customer.id,
        return_url=body.return_url,
    )
    return {"portal_url": session.url}


@app.post("/api/billing/webhook")
@limiter.limit("120/minute")
async def billing_webhook(request: Request, stripe_signature: str = Header(default="", alias="Stripe-Signature")):
    payload = await request.body()
    if not STRIPE_WEBHOOK:
        _audit_security_event(request, "billing.webhook_missing_secret")
        raise HTTPException(status_code=500, detail="Webhook secret is not configured.")

    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=stripe_signature, secret=STRIPE_WEBHOOK)
    except Exception as e:
        _audit_security_event(request, "billing.webhook_invalid_signature", str(e))
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {e}")

    event_id = str(event.get("id") or "").strip()
    now_ts = int(time.time())
    processed = _growth_db.setdefault("stripe_webhook_events", {})
    if not isinstance(processed, dict):
        processed = {}
    # Prune stale event IDs to keep storage bounded.
    cutoff = now_ts - max(300, WEBHOOK_EVENT_TTL_SECONDS)
    processed = {k: int(v) for k, v in processed.items() if str(k) and int(v) >= cutoff}
    if event_id:
        if event_id in processed:
            _growth_db["stripe_webhook_events"] = processed
            _save_growth_db()
            _audit_security_event(request, "billing.webhook_duplicate", event_id)
            return {"received": True, "duplicate": True}
        processed[event_id] = now_ts
    _growth_db["stripe_webhook_events"] = processed

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    user_id = ""
    resolved_plan = PLAN_FREE

    if event_type == "checkout.session.completed":
        user_id = _normalize_user_id(obj.get("client_reference_id") or obj.get("metadata", {}).get("userId") or "")
        meta_tier = normalize_plan_name(obj.get("metadata", {}).get("tier", ""))
        if meta_tier in {PLAN_PREMIUM, PLAN_VIP}:
            resolved_plan = meta_tier
        else:
            resolved_plan = PLAN_PREMIUM
    elif event_type.startswith("customer.subscription."):
        customer_id = obj.get("customer")
        try:
            if customer_id:
                customer = await asyncio.to_thread(stripe.Customer.retrieve, customer_id)
                user_id = _normalize_user_id(customer.get("metadata", {}).get("userId") or "")
        except Exception:
            user_id = ""
        status = str(obj.get("status") or "").strip().lower()
        if status in {"active", "trialing", "past_due", "unpaid"}:
            items = obj.get("items", {}).get("data", [])
            price_id = ""
            if isinstance(items, list) and items:
                first = items[0] if isinstance(items[0], dict) else {}
                price = first.get("price", {}) if isinstance(first, dict) else {}
                if isinstance(price, dict):
                    price_id = str(price.get("id") or "").strip()
            resolved_plan = _plan_from_price_id(price_id) if price_id else PLAN_PREMIUM
        else:
            resolved_plan = PLAN_FREE

    if user_id:
        _set_billing_entitlement(user_id, resolved_plan, source=event_type)
        _invalidate_plan_cache(user_id)
    _audit_security_event(request, "billing.webhook_processed", event_type, user_id=user_id)
    _save_growth_db()

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


@app.get("/api/live")
async def live_mode(request: Request):
    user_plan = await _get_verified_plan(request)
    tier = TIER_CONFIG.get(user_plan, TIER_CONFIG[PLAN_FREE])
    now = datetime.utcnow()
    rows: list[dict[str, Any]] = []
    for sport in tier.get("sports_allowed") or SPORTS:
        games = await fetch_odds_api_games(sport)
        for g in games:
            ct = str(g.get("commence_time") or "")
            try:
                dt = datetime.fromisoformat(ct.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                continue
            minutes = int((now - dt).total_seconds() / 60)
            if minutes < -15 or minutes > 180:
                continue
            rows.append(
                {
                    "sport": SPORT_META.get(sport, {}).get("label", sport),
                    "game": f"{g.get('away_team','')} @ {g.get('home_team','')}",
                    "minutes_since_start": minutes,
                    "home_ml": g.get("home_ml"),
                    "away_ml": g.get("away_ml"),
                    "home_team": g.get("home_team"),
                    "away_team": g.get("away_team"),
                }
            )
    rows.sort(key=lambda x: abs(int(x.get("minutes_since_start", 0))))
    max_rows = 20 if plan_rank(user_plan) >= plan_rank(PLAN_PREMIUM) else 6
    return {"live": rows[:max_rows], "count": len(rows)}


@app.get("/api/news-impact")
async def news_impact(request: Request):
    user_plan = await _get_verified_plan(request)
    if plan_rank(user_plan) < plan_rank(PLAN_PREMIUM):
        raise HTTPException(status_code=403, detail="Upgrade to Premium for news impact feed.")
    scans: list[dict[str, Any]] = []
    for sport in ["basketball_nba", "americanfootball_nfl"]:
        games = await fetch_odds_api_games(sport)
        for g in games[:25]:
            by_book = g.get("moneyline_by_book") or {}
            if len(by_book) < 2:
                continue
            h = [int(v.get("home")) for v in by_book.values() if v.get("home") is not None]
            a = [int(v.get("away")) for v in by_book.values() if v.get("away") is not None]
            if len(h) < 2 or len(a) < 2:
                continue
            dispersion = max(h) - min(h) + max(a) - min(a)
            impact = round(min(100.0, max(0.0, abs(dispersion) / 4.0)), 1)
            if impact < 20:
                continue
            scans.append(
                {
                    "sport": SPORT_META.get(sport, {}).get("label", sport),
                    "game": f"{g.get('away_team','')} @ {g.get('home_team','')}",
                    "impact_score": impact,
                    "note": "Market dispersion spike suggests lineup/news re-pricing.",
                }
            )
    scans.sort(key=lambda x: x.get("impact_score", 0), reverse=True)
    return {"impacts": scans[:30], "count": len(scans)}


@app.get("/api/clv")
async def clv_status(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    return {"clv": _clv_report(rec)}


@app.post("/api/bankroll/plan")
@limiter.limit("30/hour")
async def bankroll_plan(body: BankrollPlanRequest, request: Request):
    bankroll = max(10.0, float(body.bankroll or 0.0))
    mode = str(body.risk_mode or "standard").strip().lower()
    if mode not in {"conservative", "standard", "aggressive"}:
        mode = "standard"
    unit_pct = 0.01 if mode == "standard" else (0.0075 if mode == "conservative" else 0.015)
    unit = round(bankroll * unit_pct, 2)
    max_open = max(1, min(int(body.max_open_bets or 8), 20))
    cap_pct = 0.06 if mode == "conservative" else (0.10 if mode == "standard" else 0.14)
    exposure_cap = round(bankroll * cap_pct, 2)
    return {
        "plan": {
            "bankroll": bankroll,
            "risk_mode": mode,
            "unit_size": unit,
            "max_open_bets": max_open,
            "max_exposure_dollars": exposure_cap,
            "stop_loss_daily_dollars": round(bankroll * 0.03, 2),
        }
    }


@app.get("/api/journal")
async def journal_list(request: Request, limit: int = 100):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    rows = list(rec.get("journal", []))
    rows.sort(key=lambda x: x.get("ts", 0), reverse=True)
    safe = max(1, min(int(limit or 100), 500))
    return {"entries": rows[:safe], "count": len(rows)}


@app.post("/api/journal/add")
@limiter.limit("60/hour")
async def journal_add(body: JournalEntryRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    result = str(body.result or "open").strip().lower()
    if result not in {"open", "win", "loss", "push"}:
        result = "open"
    entry = {
        "id": hashlib.md5(f"{user_id}|{time.time_ns()}".encode("utf-8")).hexdigest()[:14],
        "ts": int(time.time()),
        "game": str(body.game or "").strip()[:120],
        "bet": str(body.bet or "").strip()[:140],
        "odds": str(body.odds or "").strip()[:24],
        "stake_units": round(max(0.01, float(body.stake_units or 1.0)), 2),
        "result": result,
        "notes": str(body.notes or "").strip()[:300],
    }
    rows = rec.setdefault("journal", [])
    rows.append(entry)
    rec["journal"] = rows[-1000:]
    _save_growth_db()
    return {"ok": True, "entry": entry}


@app.get("/api/journal/export.csv")
async def journal_export_csv(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    rows = list(rec.get("journal", []))
    rows.sort(key=lambda x: x.get("ts", 0))
    lines = ["id,ts,game,bet,odds,stake_units,result,notes"]
    for r in rows:
        vals = [
            str(r.get("id", "")),
            str(r.get("ts", "")),
            str(r.get("game", "")).replace(",", " "),
            str(r.get("bet", "")).replace(",", " "),
            str(r.get("odds", "")),
            str(r.get("stake_units", "")),
            str(r.get("result", "")),
            str(r.get("notes", "")).replace(",", " "),
        ]
        lines.append(",".join(vals))
    return PlainTextResponse("\n".join(lines), media_type="text/csv")


@app.get("/api/pick/timeline")
async def pick_timeline(request: Request, key: str):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    rows = list(rec.get("tracked_picks", []))
    item = next((r for r in rows if str(r.get("key", "")) == str(key)), None)
    if not item:
        raise HTTPException(status_code=404, detail="Pick not found.")
    return {"timeline": _timeline_for_pick(item), "pick": item}


@app.get("/api/agents/weights")
async def agent_weights_get(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    return {"weights": rec.get("agent_weights", {})}


@app.post("/api/agents/weights")
@limiter.limit("30/hour")
async def agent_weights_set(body: AgentWeightsRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    data = {
        "best_line_ev": max(0.2, min(3.0, float(body.best_line_ev or 1.0))),
        "market_consensus": max(0.2, min(3.0, float(body.market_consensus or 1.0))),
        "devig": max(0.2, min(3.0, float(body.devig or 1.0))),
        "steam": max(0.2, min(3.0, float(body.steam or 1.0))),
        "fades": max(0.2, min(3.0, float(body.fades or 1.0))),
    }
    rec["agent_weights"] = data
    _save_growth_db()
    return {"ok": True, "weights": data}


@app.get("/api/alerts/packs")
async def alerts_packs_get(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    return {"packs": _ALERT_PACKS, "enabled": rec.get("alert_packs", [])}


@app.post("/api/alerts/packs")
@limiter.limit("20/hour")
async def alerts_packs_set(body: AlertPackRequest, request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    if body.pack not in _ALERT_PACKS:
        raise HTTPException(status_code=400, detail="Unknown alert pack.")
    rec = _ensure_growth_user(user_id)
    enabled = set(rec.get("alert_packs", []))
    if body.enabled:
        enabled.add(body.pack)
    else:
        enabled.discard(body.pack)
    rec["alert_packs"] = sorted(enabled)
    _save_growth_db()
    return {"ok": True, "enabled": rec["alert_packs"]}


@app.get("/api/affiliate/status")
async def affiliate_status(request: Request):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    code = rec.get("referral_code")
    clicks = list(rec.get("affiliate_clicks", []))
    clicks_30d = [c for c in clicks if int(c.get("ts", 0) or 0) >= int(time.time()) - 30 * 86400]
    refs = list(rec.get("referrals", []))
    return {
        "affiliate": {
            "code": code,
            "link": f"{FRONTEND_URL.rstrip('/')}/1.html?ref={code}",
            "clicks_30d": len(clicks_30d),
            "referrals_total": len(refs),
            "estimated_conversion_pct": round((len(refs) / max(1, len(clicks_30d))) * 100.0, 1),
        }
    }


@app.post("/api/affiliate/click")
@limiter.limit("120/hour")
async def affiliate_click(request: Request, code: str = ""):
    code_u = str(code or "").strip().upper()
    owner = _find_user_by_ref_code(code_u)
    if not owner:
        raise HTTPException(status_code=404, detail="Referral code not found.")
    rec = _ensure_growth_user(owner)
    arr = rec.setdefault("affiliate_clicks", [])
    arr.append({"ts": int(time.time()), "ip": _client_ip(request)[:80]})
    rec["affiliate_clicks"] = arr[-20000:]
    _save_growth_db()
    return {"ok": True}


@app.get("/scan")
@limiter.limit("90/hour")
async def scan(request: Request):
    """Main scan endpoint - generates picks + shows all upcoming games."""
    if not SCAN_ENABLED:
        raise HTTPException(status_code=503, detail="Scanning is temporarily disabled.")
    user_plan = await _get_verified_plan(request)
    tier = TIER_CONFIG.get(user_plan, TIER_CONFIG[PLAN_FREE])
    user_id = _request_user_id(request) or "anon"
    user_growth = _ensure_growth_user(user_id)
    agent_weights = user_growth.get("agent_weights", {})
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
    
    # Low-quota mode: serve freshest cached scan payload instead of hard-failing.
    if _quota_remaining < ODDS_MIN_REMAINING_TO_SCAN and ODDS_API_KEY:
        cached_payload = _latest_cached_scan_payload(user_id)
        if isinstance(cached_payload, dict):
            payload = dict(cached_payload)
            scan_policy = dict(payload.get("scan_policy") or {})
            scan_policy["served_from_cache"] = True
            scan_policy["quota_soft_limited"] = True
            payload["scan_policy"] = scan_policy
            payload["quota_remaining"] = _quota_remaining
            payload["quota_soft_limited"] = True
            return payload
    
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
    
    # Sort by personalized agent-weighted score.
    for p in all_picks:
        p["agent_score"] = round(_agent_weighted_pick_score(p, agent_weights), 3)
        p["betslip_url"] = _build_betslip_url(p.get("book"), p.get("game"), p.get("bet"), p.get("odds"))
    all_picks.sort(key=lambda x: x.get("agent_score", x.get("edge", 0)), reverse=True)
    fallback_mode = False
    if not all_picks and all_games:
        fallback_mode = True
        all_picks = _fallback_picks_from_games(all_games, max_count=max(8, int(tier.get("scan_pick_limit", 3) or 3)))
    
    # Sort games by time
    all_games.sort(key=lambda x: x.get("commence_time", ""))
    line_lookup = _current_line_lookup(all_games)
    
    pick_limit = int(tier.get("scan_pick_limit", 3))
    visible_picks = all_picks[:pick_limit]

    response_payload = {
        "plan": user_plan,
        "picks": visible_picks,
        "picks_total": len(all_picks),
        "fallback_mode": fallback_mode,
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
    history = user_growth.setdefault("history", [])
    history.append(history_row)
    if len(history) > 120:
        user_growth["history"] = history[-120:]

    # Track visible picks for grading/ROI.
    tracked = user_growth.setdefault("tracked_picks", [])
    existing_keys = {tp.get("key") for tp in tracked}
    for p in visible_picks:
        bet_text = str(p.get("bet") or "")
        pick_side = bet_text.replace(" ML", "").strip()
        tp = {
            "key": _pick_tracking_key(p),
            "ts": int(now_ts),
            "sport": p.get("sport"),
            "game": p.get("game"),
            "bet": p.get("bet"),
            "bet_type": p.get("bet_type") or _market_from_bet_text(p.get("bet")),
            "odds": p.get("odds"),
            "line_at_pick": p.get("odds"),
            "closing_line": None,
            "pick_side": pick_side,
            "ev": p.get("ev"),
            "status": "open",
            "units": 0.0,
            "game_time": p.get("game_time"),
        }
        if tp["key"] not in existing_keys:
            tracked.append(tp)
            existing_keys.add(tp["key"])
    if len(tracked) > 700:
        user_growth["tracked_picks"] = tracked[-700:]

    # Auto-settle completed picks using recent scores.
    scores_rows: list[dict[str, Any]] = []
    for sport in set(allowed_sports):
        try:
            scores_rows.extend(await fetch_odds_api_scores(sport, days_from=3))
        except Exception:
            continue
    _settle_user_tracked_picks(user_growth, scores_rows, line_lookup=line_lookup)

    # Attach live performance + streak for richer UI.
    response_payload["performance"] = _performance_summary(user_growth)
    response_payload["streak"] = _scan_streak_status(user_growth)

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
    used = int(_quota_used_last or 0)
    cap = int(PROPS_MONTHLY_CREDIT_CAP or 0)
    pct = round((used / cap) * 100.0, 1) if cap > 0 and used > 0 else 0.0
    return {
        "quota_remaining": _quota_remaining,
        "quota_used_last": _quota_used_last,
        "data_source": "The Odds API" if ODDS_API_KEY else "Not configured",
        "cache_ttl_seconds": CACHE_TTL,
        "props_monthly_credit_cap": cap,
        "props_budget_used_pct": pct,
        "props_budget_within_limit": _props_budget_ok(),
        "props_enabled": PROPS_ENABLED,
    }


@app.get("/api/debug/ingestion")
async def debug_ingestion(request: Request):
    """
    Quick ingestion diagnostics per sport.
    """
    _require_debug_access(request)
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
async def get_all_games(day_offset: int = 0, limit: int = 400):
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
    if not all_games:
        cached_payload = _latest_cached_scan_payload()
        if isinstance(cached_payload, dict):
            fallback_games = []
            for g in (cached_payload.get("games") or []):
                if not isinstance(g, dict):
                    continue
                sport = g.get("sport", "")
                meta = SPORT_META.get(sport, {"label": sport, "emoji": "🎯"})
                fallback_games.append({
                    "sport": sport,
                    "emoji": g.get("emoji") or meta.get("emoji", "🎯"),
                    "label": g.get("label") or meta.get("label", sport),
                    "home_team": g.get("home_team"),
                    "away_team": g.get("away_team"),
                    "game": f"{g.get('away_team', 'TBD')} @ {g.get('home_team', 'TBD')}",
                    "game_time": g.get("commence_time"),
                    "home_ml": g.get("home_ml"),
                    "away_ml": g.get("away_ml"),
                    "home_spread": g.get("home_spread"),
                    "away_spread": g.get("away_spread"),
                    "total": g.get("total"),
                    "home_edge": g.get("home_edge", 0),
                    "away_edge": g.get("away_edge", 0),
                    "books": g.get("bookmakers", []),
                })
            fallback_games.sort(key=lambda x: x.get("game_time", ""))
            all_games = fallback_games
    safe_limit = max(1, min(int(limit or 400), 1000))

    if int(day_offset or 0) != 0:
      try:
          tz = ZoneInfo(APP_TIMEZONE)
      except Exception:
          tz = ZoneInfo("America/New_York")
      target_day = (datetime.now(tz) + timedelta(days=int(day_offset))).date()
      filtered = []
      for g in all_games:
          gt = str(g.get("game_time") or "")
          if not gt:
              continue
          try:
              dt = datetime.fromisoformat(gt.replace("Z", "+00:00")).astimezone(tz)
          except Exception:
              continue
          if dt.date() == target_day:
              filtered.append(g)
      all_games = filtered
    
    return {
        "games": all_games[:safe_limit],
        "total": len(all_games),
        "sports_covered": SPORTS,
        "day_offset": int(day_offset or 0),
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


@app.get("/api/props")
async def props_lite(
    request: Request,
    sport: str = "basketball_nba",
):
    if not SCAN_ENABLED:
        raise HTTPException(status_code=503, detail="Props are temporarily disabled.")
    user_plan = await _get_verified_plan(request)
    if plan_rank(user_plan) < plan_rank(PLAN_PREMIUM):
        raise HTTPException(status_code=403, detail="Upgrade to Premium to access Props.")
    if not PROPS_ENABLED:
        raise HTTPException(status_code=503, detail="Props are temporarily disabled.")
    if sport not in PROPS_MARKETS_BY_SPORT:
        raise HTTPException(status_code=400, detail="Props currently supported for NBA and NFL.")
    if not _props_budget_ok():
        raise HTTPException(status_code=402, detail="Props budget limit reached for this month.")
    rows = await fetch_props_lite_for_sport(
        sport_key=sport,
        max_events=PROPS_MAX_EVENTS_PER_SPORT,
        markets=PROPS_MARKETS_BY_SPORT.get(sport, []),
    )
    return {
        "sport": sport,
        "props": rows,
        "count": len(rows),
        "budget": {
            "monthly_credit_cap": PROPS_MONTHLY_CREDIT_CAP,
            "quota_used_last": _quota_used_last,
            "quota_remaining": _quota_remaining,
            "within_budget": _props_budget_ok(),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY ENDPOINTS (kept for compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/injuries")
async def injuries():
    """Placeholder - injuries would need ESPN or other source."""
    return {"injuries": [], "note": "Injury data not available in v5 (cost optimization)"}


@app.get("/api/performance")
async def performance(request: Request, settle: bool = True):
    user_id = _request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=400, detail="x-user-id is required.")
    rec = _ensure_growth_user(user_id)
    if settle:
        scores_rows: list[dict[str, Any]] = []
        latest_line_lookup: dict[str, dict[str, Any]] = {}
        for sport in SPORTS:
            try:
                scores_rows.extend(await fetch_odds_api_scores(sport, days_from=3))
                games = await fetch_odds_api_games(sport)
                latest_line_lookup.update(_current_line_lookup(games))
            except Exception:
                continue
        _settle_user_tracked_picks(rec, scores_rows, line_lookup=latest_line_lookup)
        _save_growth_db()
    return {
        "overall": _performance_summary(rec),
        "streak": _scan_streak_status(rec),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
