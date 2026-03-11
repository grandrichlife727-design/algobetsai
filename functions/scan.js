import { getScanPayload } from "./_lib/odds-engine.js";
import { attachSplitsToPicks, loadTicketSplits } from "./_lib/ticket-splits.js";
const UPSTREAM = "https://algobetsai.onrender.com";
const PLAN_FREE = "free";
const PLAN_PREMIUM = "premium";
const PLAN_VIP = "vip";
const LIVE_SOURCE_ALLOW = new Set(["picks", "vip-execution", "vip"]);
const DEFAULT_CORE_SPORTS = ["basketball_nba", "icehockey_nhl", "baseball_mlb"];
const DEFAULT_FULL_SPORTS = [
  "basketball_nba",
  "americanfootball_nfl",
  "icehockey_nhl",
  "basketball_ncaab",
  "baseball_mlb",
  "soccer_epl",
  "soccer_spain_la_liga",
];
const userHeartbeat = new Map();
let lastVipLiveRefreshAt = 0;
let lastUpstreamFallbackAt = 0;
const SHARED_CACHE_VERSION = "v9";

function normalizePlan(plan) {
  const p = String(plan || "").trim().toLowerCase();
  if (p === "vip" || p === "sharp") return PLAN_VIP;
  if (p === "premium" || p === "pro") return PLAN_PREMIUM;
  return PLAN_FREE;
}

function parseCsvList(raw, fallback) {
  const parts = String(raw || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  return parts.length ? parts : fallback.slice();
}

function touchHeartbeat(userId, nowMs) {
  if (!userId) return;
  userHeartbeat.set(userId, nowMs);
  const cutoff = nowMs - (60 * 60 * 1000);
  for (const [uid, ts] of userHeartbeat.entries()) {
    if (ts < cutoff) userHeartbeat.delete(uid);
  }
}

function hasRecentActivity(nowMs, activeWindowMs) {
  for (const ts of userHeartbeat.values()) {
    if ((nowMs - ts) <= activeWindowMs) return true;
  }
  return false;
}

function testingLiveOverride(env, userId) {
  if (String(env?.TESTING_ALLOW_LIVE || "").trim().toLowerCase() === "true") return true;
  const allowId = String(env?.TESTING_LIVE_USER_ID || "").trim().toLowerCase();
  return !!allowId && !!userId && allowId === String(userId || "").trim().toLowerCase();
}

function ttlForPlanSeconds(plan, env) {
  if (plan === PLAN_VIP) return Math.max(60, Number(env?.SCAN_CACHE_TTL_VIP_SECONDS || 20 * 60));
  if (plan === PLAN_PREMIUM) return Math.max(60, Number(env?.SCAN_CACHE_TTL_PREMIUM_SECONDS || 2 * 60 * 60));
  return Math.max(60, Number(env?.SCAN_CACHE_TTL_FREE_SECONDS || 6 * 60 * 60));
}

function cacheBucketForPlan(plan) {
  if (plan === PLAN_VIP) return PLAN_VIP;
  return PLAN_FREE;
}

function normalizeBoardDay() {
  return "today";
}

function buildSharedCacheRequest(requestUrl, plan, sports, boardDay) {
  const u = new URL(requestUrl);
  u.pathname = `/__scan_cache/${SHARED_CACHE_VERSION}/${cacheBucketForPlan(plan)}`;
  u.search = "";
  u.searchParams.set("sports", sports.join(","));
  u.searchParams.set("board_day", normalizeBoardDay(boardDay));
  return new Request(u.toString(), { method: "GET" });
}

function buildNonEmptyCacheRequest(requestUrl, plan, sports, boardDay) {
  const u = new URL(requestUrl);
  u.pathname = `/__scan_cache_nonempty/${SHARED_CACHE_VERSION}/${cacheBucketForPlan(plan)}`;
  u.search = "";
  u.searchParams.set("sports", sports.join(","));
  u.searchParams.set("board_day", normalizeBoardDay(boardDay));
  return new Request(u.toString(), { method: "GET" });
}

function buildModelSummaryRequest(requestUrl, dateKey) {
  const u = new URL(requestUrl);
  u.pathname = `/__model_summary/${dateKey}`;
  u.search = "";
  return new Request(u.toString(), { method: "GET" });
}

function buildModelSummary(payload) {
  const picks = Array.isArray(payload?.picks) ? payload.picks : [];
  const bySport = {};
  const byMarket = {};
  let evSum = 0;
  let evCount = 0;
  let confSum = 0;
  let confCount = 0;
  picks.forEach((p) => {
    const sport = String(p?.sport || p?.sk || "").trim().toLowerCase();
    if (sport) bySport[sport] = Number(bySport[sport] || 0) + 1;
    const market = String(p?.market || p?.type || "").trim().toLowerCase();
    if (market) byMarket[market] = Number(byMarket[market] || 0) + 1;
    const ev = Number(p?.ev || p?.edge || 0);
    if (Number.isFinite(ev) && ev !== 0) {
      evSum += ev;
      evCount += 1;
    }
    const conf = Number(p?.confidence || p?.conf || 0);
    if (Number.isFinite(conf) && conf !== 0) {
      confSum += conf;
      confCount += 1;
    }
  });
  return {
    date: new Date().toISOString().slice(0, 10),
    generated_at: new Date().toISOString(),
    picks_total: picks.length,
    avg_edge: evCount ? Number((evSum / evCount).toFixed(2)) : 0,
    avg_confidence: confCount ? Number((confSum / confCount).toFixed(2)) : 0,
    by_sport: bySport,
    by_market: byMarket,
  };
}

async function writeModelSummary(request, summary) {
  if (!summary || !summary.date) return;
  const cache = caches.default;
  const key = buildModelSummaryRequest(request.url, summary.date);
  const ttlSec = 7 * 24 * 60 * 60;
  const resp = new Response(JSON.stringify(summary), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${ttlSec}`,
      "x-algobets-cache-created-at-ms": String(Date.now()),
      "x-algobets-cache-ttl-seconds": String(ttlSec),
      "x-algobets-origin": "cloudflare-model-summary",
    },
  });
  await cache.put(key, resp);
}

async function readSharedCache(request, plan, sports, boardDay, nowMs) {
  const cache = caches.default;
  const key = buildSharedCacheRequest(request.url, plan, sports, boardDay);
  const hit = await cache.match(key);
  if (!hit) return null;
  const createdAtMs = Number(hit.headers.get("x-algobets-cache-created-at-ms") || 0);
  const ttlSec = Number(hit.headers.get("x-algobets-cache-ttl-seconds") || 0);
  const payload = await hit.json().catch(() => null);
  if (!payload || !createdAtMs || !ttlSec) return null;
  const ageSeconds = Math.max(0, Math.floor((nowMs - createdAtMs) / 1000));
  return {
    fresh: ageSeconds <= ttlSec,
    ageSeconds,
    ttlSec,
    payload,
  };
}

async function writeSharedCache(request, plan, sports, boardDay, ttlSec, payload, nowMs) {
  const cache = caches.default;
  const key = buildSharedCacheRequest(request.url, plan, sports, boardDay);
  const body = JSON.stringify(payload);
  const resp = new Response(body, {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${ttlSec}`,
      "x-algobets-cache-created-at-ms": String(nowMs),
      "x-algobets-cache-ttl-seconds": String(ttlSec),
      "x-algobets-origin": "cloudflare-scan-cache",
    },
  });
  await cache.put(key, resp);
}

async function readNonEmptyCache(request, plan, sports, boardDay, nowMs) {
  const cache = caches.default;
  const key = buildNonEmptyCacheRequest(request.url, plan, sports, boardDay);
  const hit = await cache.match(key);
  if (!hit) return null;
  const createdAtMs = Number(hit.headers.get("x-algobets-cache-created-at-ms") || 0);
  const ttlSec = Number(hit.headers.get("x-algobets-cache-ttl-seconds") || 0);
  const payload = await hit.json().catch(() => null);
  if (!payload || !createdAtMs || !ttlSec) return null;
  const ageSeconds = Math.max(0, Math.floor((nowMs - createdAtMs) / 1000));
  return { fresh: ageSeconds <= ttlSec, ageSeconds, ttlSec, payload };
}

async function writeNonEmptyCache(request, plan, sports, boardDay, ttlSec, payload, nowMs) {
  if (!(Number(payload?.games_total || 0) > 0 || Number(payload?.picks_total || 0) > 0)) return;
  const cache = caches.default;
  const key = buildNonEmptyCacheRequest(request.url, plan, sports, boardDay);
  const resp = new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${ttlSec}`,
      "x-algobets-cache-created-at-ms": String(nowMs),
      "x-algobets-cache-ttl-seconds": String(ttlSec),
      "x-algobets-origin": "cloudflare-scan-nonempty-cache",
    },
  });
  await cache.put(key, resp);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-algobets-origin": "cloudflare-scan",
    },
  });
}

function applyPlanPaywall(payload, plan) {
  if (!payload || typeof payload !== "object") return payload;
  const normalized = normalizePlan(plan);
  const totalPicks = Number(payload?.picks_total || (Array.isArray(payload?.picks) ? payload.picks.length : 0) || 0);
  if (normalized === PLAN_VIP) {
    payload.paywall = {
      visible_pick_limit: totalPicks,
      total_picks: totalPicks,
      next_tier: null,
    };
    return payload;
  }
  if (normalized === PLAN_PREMIUM) {
    payload.paywall = {
      visible_pick_limit: totalPicks,
      total_picks: totalPicks,
      next_tier: "vip",
    };
    return payload;
  }
  payload.paywall = {
    visible_pick_limit: Math.min(3, totalPicks),
    total_picks: totalPicks,
    next_tier: "premium",
  };
  return payload;
}

export async function onRequestGet(context) {
  try {
    const url = new URL(context.request.url);
    const nowMs = Date.now();
    const forceRequested = String(url.searchParams.get("refresh") || "").toLowerCase() === "true";
    const source = String(url.searchParams.get("source") || "").trim().toLowerCase();
    const boardDay = normalizeBoardDay(url.searchParams.get("board_day") || "today");
    const userId = String(context.request.headers.get("x-user-id") || "").trim().toLowerCase();
    const plan = normalizePlan(
      context.request.headers.get("x-user-plan") || PLAN_FREE,
    );

    const activeWindowMs = Math.max(1, Number(context.env?.ACTIVITY_WINDOW_MINUTES || 15)) * 60 * 1000;
    const vipLiveCooldownMs = Math.max(30, Number(context.env?.VIP_LIVE_SCAN_COOLDOWN_SECONDS || 240)) * 1000;
    const liveSourceEligible = LIVE_SOURCE_ALLOW.has(source);

    touchHeartbeat(userId, nowMs);
    const activityPassed = hasRecentActivity(nowMs, activeWindowMs) || testingLiveOverride(context.env, userId);
    const canRequestLive = forceRequested && liveSourceEligible && activityPassed;
    const vipCanForceLive = plan === PLAN_VIP && canRequestLive && (nowMs - lastVipLiveRefreshAt) >= vipLiveCooldownMs;
    if (vipCanForceLive) lastVipLiveRefreshAt = nowMs;

    const coreSports = parseCsvList(context.env?.CORE_SCAN_SPORTS, DEFAULT_CORE_SPORTS);
    const fullSports = parseCsvList(context.env?.FULL_SCAN_SPORTS, DEFAULT_FULL_SPORTS);
    const sports = (plan === PLAN_VIP || boardDay === "tomorrow") ? fullSports : coreSports;
    const sharedTtlSec = ttlForPlanSeconds(plan, context.env);
    const sharedCache = await readSharedCache(context.request, plan, sports, boardDay, nowMs);
    const nonEmptyCache = await readNonEmptyCache(context.request, plan, sports, boardDay, nowMs);

    const shouldServeCachedBoard = !!sharedCache && (
      plan !== PLAN_VIP
      || (!vipCanForceLive && sharedCache.fresh)
      || (plan !== PLAN_VIP && !!sharedCache.payload)
    );

    if (shouldServeCachedBoard) {
      const cachedPayload = structuredClone(sharedCache.payload);
      applyPlanPaywall(cachedPayload, plan);
      cachedPayload.scan_policy = {
        ...(cachedPayload.scan_policy || {}),
        requested_refresh: forceRequested,
        source: source || "unknown",
        board_day: boardDay,
        live_source_eligible: liveSourceEligible,
        activity_check_passed: activityPassed,
        activity_window_minutes: Math.round(activeWindowMs / 60000),
        vip_live_cooldown_seconds: Math.round(vipLiveCooldownMs / 1000),
        forced_live_fetch: false,
        live_fetch_allowed: plan === PLAN_VIP && canRequestLive,
        plan,
        served_from_cache: true,
        served_stale_cache: !sharedCache.fresh,
        shared_cache_age_seconds: sharedCache.ageSeconds,
        shared_cache_ttl_seconds: sharedCache.ttlSec,
      };
      return json(cachedPayload, 200);
    }

    const payload = await getScanPayload(context.env, {
      force: vipCanForceLive,
      sports,
      boardDay,
    });
    const localHasData = Number(payload?.games_total || 0) > 0 || Number(payload?.picks_total || 0) > 0;
    const basePolicy = {
      ...(payload.scan_policy || {}),
      requested_refresh: forceRequested,
      source: source || "unknown",
      live_source_eligible: liveSourceEligible,
      activity_check_passed: activityPassed,
      activity_window_minutes: Math.round(activeWindowMs / 60000),
      vip_live_cooldown_seconds: Math.round(vipLiveCooldownMs / 1000),
      forced_live_fetch: vipCanForceLive,
      live_fetch_allowed: plan === PLAN_VIP && canRequestLive,
      plan,
    };
    if (localHasData) {
      const splits = await loadTicketSplits(context.env);
      payload.picks = attachSplitsToPicks(payload.picks, splits);
      applyPlanPaywall(payload, plan);
      payload.scan_policy = {
        ...basePolicy,
        served_from_cache: false,
        shared_cache_age_seconds: 0,
        shared_cache_ttl_seconds: sharedTtlSec,
      };
      await writeSharedCache(context.request, plan, sports, boardDay, sharedTtlSec, payload, nowMs);
      await writeNonEmptyCache(context.request, plan, sports, boardDay, sharedTtlSec, payload, nowMs);
      if (!payload.scan_policy?.served_from_cache) {
        await writeModelSummary(context.request, buildModelSummary(payload));
      }
      return json(payload, 200);
    }

    // Upstream fallback is VIP-only and throttled.
    const allowUpstreamFallback = plan === PLAN_VIP
      && canRequestLive
      && (nowMs - lastUpstreamFallbackAt) >= vipLiveCooldownMs;

    if (allowUpstreamFallback) {
      lastUpstreamFallbackAt = nowMs;
      const upstreamUrl = new URL("/scan", UPSTREAM);
      if (forceRequested) upstreamUrl.searchParams.set("refresh", "true");
      const headers = new Headers();
      const auth = String(context.request.headers.get("authorization") || "").trim();
      if (auth) headers.set("authorization", auth);
      if (userId) headers.set("x-user-id", userId);
      headers.set("x-user-plan", plan);
      const upstreamKey = String(context.env?.BACKEND_API_KEY || "").trim();
      if (upstreamKey) headers.set("x-api-key", upstreamKey);

      const upstreamRes = await fetch(upstreamUrl.toString(), { method: "GET", headers });
      if (upstreamRes.ok) {
        const upstreamPayload = await upstreamRes.json().catch(() => null);
        if (upstreamPayload && typeof upstreamPayload === "object") {
          const splits = await loadTicketSplits(context.env);
          upstreamPayload.picks = attachSplitsToPicks(upstreamPayload.picks, splits);
          applyPlanPaywall(upstreamPayload, plan);
          upstreamPayload.scan_policy = {
            ...(upstreamPayload.scan_policy || {}),
            ...basePolicy,
            served_from_cloudflare_fallback: true,
            served_from_cache: false,
            shared_cache_age_seconds: 0,
            shared_cache_ttl_seconds: sharedTtlSec,
          };
          if (Number(upstreamPayload?.games_total || 0) > 0 || Number(upstreamPayload?.picks_total || 0) > 0) {
            await writeSharedCache(context.request, plan, sports, boardDay, sharedTtlSec, upstreamPayload, nowMs);
            await writeNonEmptyCache(context.request, plan, sports, boardDay, sharedTtlSec, upstreamPayload, nowMs);
            await writeModelSummary(context.request, buildModelSummary(upstreamPayload));
          }
          return json(upstreamPayload, 200);
        }
      }
      payload.scan_policy = {
        ...basePolicy,
        served_from_cloudflare_fallback: false,
        fallback_error: `upstream_scan_${Number(upstreamRes?.status || 0)}`,
      };
    } else {
      payload.scan_policy = {
        ...basePolicy,
        served_from_cloudflare_fallback: false,
        fallback_blocked: true,
      };
    }

    const splits = await loadTicketSplits(context.env);
    payload.picks = attachSplitsToPicks(payload.picks, splits);
    applyPlanPaywall(payload, plan);
    if (nonEmptyCache?.payload) {
      const stalePayload = structuredClone(nonEmptyCache.payload);
      applyPlanPaywall(stalePayload, plan);
      stalePayload.scan_policy = {
        ...(stalePayload.scan_policy || {}),
        ...basePolicy,
        served_from_cache: true,
        served_stale_cache: true,
        served_last_nonempty_board: true,
        shared_cache_age_seconds: nonEmptyCache.ageSeconds,
        shared_cache_ttl_seconds: nonEmptyCache.ttlSec,
      };
      return json(stalePayload, 200);
    }
    if (sharedCache?.payload) {
      const stalePayload = structuredClone(sharedCache.payload);
      applyPlanPaywall(stalePayload, plan);
      stalePayload.scan_policy = {
        ...(stalePayload.scan_policy || {}),
        requested_refresh: forceRequested,
        source: source || "unknown",
        board_day: boardDay,
        live_source_eligible: liveSourceEligible,
        activity_check_passed: activityPassed,
        activity_window_minutes: Math.round(activeWindowMs / 60000),
        vip_live_cooldown_seconds: Math.round(vipLiveCooldownMs / 1000),
        forced_live_fetch: vipCanForceLive,
        live_fetch_allowed: plan === PLAN_VIP && canRequestLive,
        plan,
        served_from_cache: true,
        served_stale_cache: true,
        shared_cache_age_seconds: sharedCache.ageSeconds,
        shared_cache_ttl_seconds: sharedCache.ttlSec,
      };
      return json(stalePayload, 200);
    }
    return json(payload, 200);
  } catch (err) {
    return json(
      {
        error: "scan_unavailable",
        detail: String(err?.message || err || "Unknown error"),
        picks: [],
        picks_total: 0,
        games: [],
        games_total: 0,
        debug_has_api_key: false,
      },
      503,
    );
  }
}
