import { evFinderRows, getScanPayload } from "../_lib/odds-engine.js";
const UPSTREAM = "https://algobetsai.onrender.com";
const PLAN_FREE = "free";
const PLAN_PREMIUM = "premium";
const PLAN_VIP = "vip";
const AUTH_TTL_SECONDS = 30 * 24 * 3600;
const userState = new Map();

function toUpstreamUrl(requestUrl, pathParam) {
  const source = new URL(requestUrl);
  const raw = (Array.isArray(pathParam) ? pathParam.join("/") : String(pathParam || "")).replace(/,/g, "/");
  const path = raw.replace(/^\/+/, "");
  const normalized = path.startsWith("api/") ? path : `api/${path}`;
  const target = new URL(`${UPSTREAM}/${normalized}`);
  target.search = source.search;
  return target.toString();
}

function forwardHeaders(request) {
  const headers = new Headers(request.headers);
  const drop = [
    "host",
    "origin",
    "cf-connecting-ip",
    "cf-ipcountry",
    "cf-ray",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-real-ip",
  ];
  for (const h of drop) headers.delete(h);
  return headers;
}

export async function onRequest(context) {
  const { request, params, env } = context;
  const method = request.method.toUpperCase();
  const rawPath = (Array.isArray(params.path) ? params.path.join("/") : String(params.path || "")).replace(/,/g, "/");
  const cleanPath = rawPath.replace(/^\/+/, "");
  const nowSec = Math.floor(Date.now() / 1000);
  const userId = String(request.headers.get("x-user-id") || "").trim().toLowerCase();
  const user = userState.get(userId) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };

  const json = (body, status = 200, origin = "cloudflare-api") =>
    new Response(JSON.stringify(body), {
      status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-algobets-origin": origin,
      },
    });

  if (method === "GET" && cleanPath === "ev-finder") {
    try {
      const payload = await getScanPayload(env, { force: false });
      const rows = evFinderRows(payload);
      return json({ results: rows, count: rows.length, data_source: "odds_api" }, 200, "cloudflare-ev-finder");
    } catch (err) {
      return json({ detail: String(err?.message || err || "EV finder unavailable"), results: [], count: 0 }, 503, "cloudflare-ev-finder");
    }
  }

  if (method === "GET" && cleanPath === "config/public") {
    return json(
      {
        vip_discord_url: String(env?.VIP_DISCORD_URL || ""),
        google_client_id: String(env?.GOOGLE_CLIENT_ID || ""),
        billing_enabled: String(env?.BILLING_ENABLED || "false").toLowerCase() === "true",
        auth_required: true,
        discord_role_sync_enabled: false,
        low_data_mode_global: true,
      },
      200,
      "cloudflare-config",
    );
  }

  if (method === "POST" && cleanPath === "auth/session") {
    const body = await request.json().catch(() => ({}));
    const incomingUser = String(body?.user_id || "").trim().toLowerCase();
    const finalUser = incomingUser || `u_${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;
    const methodName = String(body?.method || "guest").trim().toLowerCase();
    const identifier = String(body?.identifier || "").trim().toLowerCase();
    const tokenPayload = btoa(JSON.stringify({ sub: finalUser, iat: nowSec, exp: nowSec + AUTH_TTL_SECONDS }));
    if (!userState.has(finalUser)) userState.set(finalUser, { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false });
    return json(
      {
        token: `cf.${tokenPayload}.sig`,
        user_id: finalUser,
        expires_in: AUTH_TTL_SECONDS,
        profile: { method: methodName || "guest", identifier },
      },
      200,
      "cloudflare-auth",
    );
  }

  if (method === "GET" && cleanPath === "plan") {
    const trialActive = Number(user.trialUntil || 0) > nowSec;
    const plan = trialActive ? PLAN_PREMIUM : (user.plan || PLAN_FREE);
    const tier = plan === PLAN_VIP
      ? { name: "VIP", scan_pick_limit: 50, max_picks: 50, min_scan_interval_seconds: 15 }
      : plan === PLAN_PREMIUM
        ? { name: "Premium", scan_pick_limit: 20, max_picks: 20, min_scan_interval_seconds: 20 }
        : { name: "Free", scan_pick_limit: 3, max_picks: 3, min_scan_interval_seconds: 25 };
    return json({ plan, tier }, 200, "cloudflare-plan");
  }

  if (method === "GET" && cleanPath === "pricing") {
    const billingEnabled = String(env?.BILLING_ENABLED || "false").toLowerCase() === "true";
    return json(
      {
        billing_enabled: billingEnabled,
        tiers: {
          free: { billing: { monthly_configured: false, annual_configured: false } },
          premium: { billing: { monthly_configured: billingEnabled, annual_configured: billingEnabled } },
          vip: { billing: { monthly_configured: billingEnabled, annual_configured: billingEnabled } },
        },
      },
      200,
      "cloudflare-pricing",
    );
  }

  if (method === "GET" && cleanPath === "trial/status") {
    const trialUntil = Number(user.trialUntil || 0);
    const secs = Math.max(0, trialUntil - nowSec);
    const claimed = !!user.trialClaimed;
    return json(
      {
        eligible: !claimed || secs > 0,
        claimed,
        claimed_at: claimed ? Math.max(0, trialUntil - (72 * 3600)) : null,
        trial_active_until: secs > 0 ? trialUntil : null,
        trial_seconds_left: secs,
      },
      200,
      "cloudflare-trial",
    );
  }

  if (method === "POST" && cleanPath === "trial/start") {
    if (!userId) return json({ detail: "x-user-id is required." }, 400, "cloudflare-trial");
    const trialUntil = nowSec + (72 * 3600);
    userState.set(userId, { ...user, trialUntil, trialClaimed: true, plan: user.plan || PLAN_FREE });
    return json(
      { ok: true, granted_hours: 72, trial_plan: PLAN_PREMIUM, trial_until: trialUntil },
      200,
      "cloudflare-trial",
    );
  }

  if (method === "POST" && (cleanPath === "billing/checkout" || cleanPath === "billing/portal" || cleanPath === "billing/cancel-trial")) {
    const billingEnabled = String(env?.BILLING_ENABLED || "false").toLowerCase() === "true";
    if (!billingEnabled) {
      return json({ detail: "Billing is temporarily disabled on Cloudflare environment." }, 503, "cloudflare-billing");
    }
    return json({ detail: "Billing migration in progress. Endpoint available but not configured." }, 503, "cloudflare-billing");
  }

  const url = toUpstreamUrl(request.url, rawPath);
  const hasApiKey = !!String(request.headers.get("x-api-key") || "").trim();
  const init = {
    method,
    headers: forwardHeaders(request),
    redirect: "follow",
  };
  const proxyApiKey = String(env?.BACKEND_API_KEY || "").trim();
  // This function only handles /api/* routes in Pages, so always inject when missing.
  if (!hasApiKey && proxyApiKey) {
    init.headers.set("x-api-key", proxyApiKey);
  }
  if (!["GET", "HEAD"].includes(method)) init.body = request.body;

  const upstream = await fetch(url, init);
  const outHeaders = new Headers(upstream.headers);
  outHeaders.set("x-algobets-proxy", "cloudflare-pages");
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: outHeaders,
  });
}
