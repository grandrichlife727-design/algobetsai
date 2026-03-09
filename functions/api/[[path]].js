import { evFinderRows, getScanPayload } from "../_lib/odds-engine.js";
import { importTicketSplits, loadTicketSplits } from "../_lib/ticket-splits.js";

const UPSTREAM = "https://algobetsai.onrender.com";
const STRIPE_BASE = "https://api.stripe.com";
const PLAN_FREE = "free";
const PLAN_PREMIUM = "premium";
const PLAN_VIP = "vip";
const AUTH_TTL_SECONDS = 30 * 24 * 3600;
const ACTIVE_SUB_STATUSES = new Set(["active", "trialing", "past_due", "unpaid"]);
const userState = new Map();

function normalizePlan(plan) {
  const p = String(plan || "").trim().toLowerCase();
  if (p === "vip" || p === "sharp") return PLAN_VIP;
  if (p === "premium" || p === "pro") return PLAN_PREMIUM;
  return PLAN_FREE;
}

function planRank(plan) {
  const p = normalizePlan(plan);
  if (p === PLAN_VIP) return 2;
  if (p === PLAN_PREMIUM) return 1;
  return 0;
}

function splitCsvEnv(value) {
  return String(value || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function json(body, status = 200, origin = "cloudflare-api") {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-algobets-origin": origin,
    },
  });
}

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

function stripeConfigured(env) {
  const secret = String(env?.STRIPE_SECRET_KEY || "").trim();
  const premium = splitCsvEnv(env?.STRIPE_PREMIUM_PRICE_IDS);
  const vip = splitCsvEnv(env?.STRIPE_VIP_PRICE_IDS);
  const premiumAnnual = splitCsvEnv(env?.STRIPE_PREMIUM_ANNUAL_PRICE_IDS);
  const vipAnnual = splitCsvEnv(env?.STRIPE_VIP_ANNUAL_PRICE_IDS);
  const enabledFlag = String(env?.BILLING_ENABLED || "true").trim().toLowerCase() !== "false";
  return {
    enabled: enabledFlag && !!secret,
    secret,
    premium,
    vip,
    premiumAnnual,
    vipAnnual,
  };
}

function defaultReturnBase(env) {
  return String(env?.FRONTEND_URL || "https://algobetsai.pages.dev/app.html").trim();
}

function allowedBillingOrigins(env) {
  const configured = splitCsvEnv(env?.BILLING_RETURN_ORIGINS);
  if (configured.length) return configured.map((x) => x.replace(/\/+$/, "").toLowerCase());
  return [
    "https://algobetsai.pages.dev",
    "https://algobetsai.onrender.com",
    "https://grandrichlife727-design.github.io",
  ];
}

function isAllowedReturnUrl(rawUrl, env) {
  try {
    const u = new URL(String(rawUrl || "").trim());
    if (!["https:", "http:"].includes(u.protocol)) return false;
    if (["localhost", "127.0.0.1"].includes(u.hostname)) return true;
    const origin = `${u.protocol}//${u.host}`.replace(/\/+$/, "").toLowerCase();
    return allowedBillingOrigins(env).includes(origin);
  } catch (_) {
    return false;
  }
}

function priceIdForTierCycle(cfg, tier, cycle = "monthly") {
  const c = String(cycle || "monthly").toLowerCase() === "annual" ? "annual" : "monthly";
  const t = normalizePlan(tier);
  if (t === PLAN_PREMIUM) {
    if (c === "annual") return cfg.premiumAnnual[0] || "";
    return cfg.premium[0] || "";
  }
  if (t === PLAN_VIP) {
    if (c === "annual") return cfg.vipAnnual[0] || "";
    return cfg.vip[0] || "";
  }
  return "";
}

function planFromPriceId(cfg, priceId) {
  const id = String(priceId || "").trim();
  if (!id) return PLAN_FREE;
  if (cfg.vip.includes(id) || cfg.vipAnnual.includes(id)) return PLAN_VIP;
  if (cfg.premium.includes(id) || cfg.premiumAnnual.includes(id)) return PLAN_PREMIUM;
  return PLAN_FREE;
}

function hexToBytes(hex) {
  const clean = String(hex || "").trim();
  if (!clean || clean.length % 2 !== 0) return new Uint8Array();
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < clean.length; i += 2) out[i / 2] = parseInt(clean.slice(i, i + 2), 16);
  return out;
}

function bytesToHex(buf) {
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function hmacSha256Hex(secret, payload) {
  const enc = new TextEncoder();
  const keyData = enc.encode(String(secret || ""));
  const cryptoKey = await crypto.subtle.importKey("raw", keyData, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, enc.encode(payload));
  return bytesToHex(sig);
}

function timingSafeEqualHex(a, b) {
  const ba = hexToBytes(a);
  const bb = hexToBytes(b);
  if (!ba.length || ba.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ba.length; i += 1) diff |= ba[i] ^ bb[i];
  return diff === 0;
}

function normalizeUserId(value) {
  const v = String(value || "").trim().toLowerCase();
  return /^[a-z0-9:_-]{6,64}$/.test(v) ? v : "";
}

async function verifyStripeWebhookSignature(env, request, rawBody) {
  const secret = String(env?.STRIPE_WEBHOOK_SECRET || "").trim();
  if (!secret) return { ok: false, detail: "Webhook secret is not configured." };
  const sigHeader = String(request.headers.get("stripe-signature") || "").trim();
  if (!sigHeader) return { ok: false, detail: "Missing Stripe signature header." };

  const parts = sigHeader.split(",").map((p) => p.trim());
  let timestamp = "";
  const v1 = [];
  for (const p of parts) {
    if (p.startsWith("t=")) timestamp = p.slice(2);
    else if (p.startsWith("v1=")) v1.push(p.slice(3));
  }
  if (!timestamp || !v1.length) return { ok: false, detail: "Invalid Stripe signature format." };

  const now = Math.floor(Date.now() / 1000);
  const ts = Number(timestamp);
  if (!Number.isFinite(ts) || Math.abs(now - ts) > 300) return { ok: false, detail: "Stripe signature timestamp outside tolerance." };

  const expected = await hmacSha256Hex(secret, `${timestamp}.${rawBody}`);
  const matched = v1.some((sig) => timingSafeEqualHex(sig, expected));
  return matched ? { ok: true } : { ok: false, detail: "Stripe signature mismatch." };
}

async function stripeRequest(env, path, method = "GET", form = null) {
  const cfg = stripeConfigured(env);
  if (!cfg.secret) throw new Error("Stripe secret is not configured.");
  const headers = {
    Authorization: `Bearer ${cfg.secret}`,
  };
  const init = { method, headers };
  if (form && method !== "GET") {
    headers["content-type"] = "application/x-www-form-urlencoded";
    init.body = new URLSearchParams(form).toString();
  }
  const res = await fetch(`${STRIPE_BASE}${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = String(data?.error?.message || `Stripe error (${res.status})`);
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return data;
}

async function stripeFindCustomerByUserId(env, userId) {
  const q = encodeURIComponent(`metadata['userId']:'${String(userId || "").replace(/'/g, "")}'`);
  const out = await stripeRequest(env, `/v1/customers/search?query=${q}&limit=1`, "GET");
  const arr = Array.isArray(out?.data) ? out.data : [];
  return arr[0] || null;
}

async function stripeFindOrCreateCustomer(env, userId) {
  const uid = String(userId || "").trim().toLowerCase();
  let customer = await stripeFindCustomerByUserId(env, uid);
  if (customer) return customer;
  const email = `${uid || "user"}@users.algobets.local`;
  customer = await stripeRequest(env, "/v1/customers", "POST", {
    email,
    "metadata[userId]": uid,
  });
  return customer;
}

async function stripeActiveSubscription(env, customerId) {
  if (!customerId) return null;
  const out = await stripeRequest(env, `/v1/subscriptions?customer=${encodeURIComponent(customerId)}&status=all&limit=20`, "GET");
  const list = (Array.isArray(out?.data) ? out.data : []).filter((s) => ACTIVE_SUB_STATUSES.has(String(s?.status || "").toLowerCase()));
  if (!list.length) return null;
  list.sort((a, b) => Number(b?.created || 0) - Number(a?.created || 0));
  return list[0];
}

function subscriptionPlan(cfg, sub) {
  const priceId = sub?.items?.data?.[0]?.price?.id || "";
  return planFromPriceId(cfg, priceId);
}

async function stripePlanForUser(env, userId) {
  const cfg = stripeConfigured(env);
  if (!cfg.enabled || !userId) return PLAN_FREE;
  const customer = await stripeFindCustomerByUserId(env, userId);
  if (!customer?.id) return PLAN_FREE;
  const sub = await stripeActiveSubscription(env, customer.id);
  if (!sub) return PLAN_FREE;
  return subscriptionPlan(cfg, sub);
}

async function resolveEffectivePlanForUser(env, userId, userRec, nowSec, clientPlanHint = PLAN_FREE) {
  const storedPlan = normalizePlan(userRec?.plan || PLAN_FREE);
  const clientPlan = normalizePlan(clientPlanHint || PLAN_FREE);
  let stripePlan = PLAN_FREE;
  try {
    stripePlan = userId ? await stripePlanForUser(env, userId) : PLAN_FREE;
  } catch (_) {
    stripePlan = PLAN_FREE;
  }
  let plan = planRank(stripePlan) >= planRank(storedPlan) ? stripePlan : storedPlan;
  if (planRank(clientPlan) > planRank(plan)) plan = clientPlan;
  const trialActive = Number(userRec?.trialUntil || 0) > nowSec;
  if (trialActive && plan === PLAN_FREE) plan = PLAN_PREMIUM;
  return normalizePlan(plan);
}

export async function onRequest(context) {
  const { request, params, env } = context;
  const method = request.method.toUpperCase();
  const rawPath = (Array.isArray(params.path) ? params.path.join("/") : String(params.path || "")).replace(/,/g, "/");
  const cleanPath = rawPath.replace(/^\/+/, "");
  const nowSec = Math.floor(Date.now() / 1000);
  const userId = String(request.headers.get("x-user-id") || "").trim().toLowerCase();
  const user = userState.get(userId) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };
  const billing = stripeConfigured(env);

  if (method === "GET" && cleanPath === "ev-finder") {
    try {
      const payload = await getScanPayload(env, { force: false });
      const rows = evFinderRows(payload);
      return json({ results: rows, count: rows.length, data_source: "odds_api" }, 200, "cloudflare-ev-finder");
    } catch (err) {
      return json({ detail: String(err?.message || err || "EV finder unavailable"), results: [], count: 0 }, 503, "cloudflare-ev-finder");
    }
  }

  if (method === "GET" && cleanPath === "splits/status") {
    const rows = await loadTicketSplits(env);
    return json(
      {
        configured: !!String(env?.TICKET_SPLITS_FEED_URL || "").trim(),
        feed_url_set: !!String(env?.TICKET_SPLITS_FEED_URL || "").trim(),
        rows_loaded: Array.isArray(rows) ? rows.length : 0,
      },
      200,
      "cloudflare-splits",
    );
  }

  if (method === "POST" && cleanPath === "splits/import") {
    const adminToken = String(env?.ADMIN_API_TOKEN || "").trim();
    const provided = String(request.headers.get("x-admin-token") || "").trim();
    if (!adminToken || provided !== adminToken) {
      return json({ detail: "Admin token required." }, 401, "cloudflare-splits");
    }
    const body = await request.json().catch(() => ({}));
    const rows = Array.isArray(body?.rows) ? body.rows : [];
    const out = importTicketSplits(rows);
    return json({ ok: true, ...out }, 200, "cloudflare-splits");
  }

  if (method === "GET" && cleanPath === "config/public") {
    return json(
      {
        vip_discord_url: String(env?.VIP_DISCORD_URL || ""),
        google_client_id: String(env?.GOOGLE_CLIENT_ID || ""),
        billing_enabled: billing.enabled,
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

  const clientPlanHint = normalizePlan(request.headers.get("x-user-plan-client") || PLAN_FREE);

  if (method === "GET" && cleanPath === "plan") {
    const plan = await resolveEffectivePlanForUser(env, userId, user, nowSec, clientPlanHint);
    const tier = plan === PLAN_VIP
      ? { name: "VIP", scan_pick_limit: 50, max_picks: 50, min_scan_interval_seconds: 15 }
      : plan === PLAN_PREMIUM
        ? { name: "Premium", scan_pick_limit: 20, max_picks: 20, min_scan_interval_seconds: 20 }
        : { name: "Free", scan_pick_limit: 3, max_picks: 3, min_scan_interval_seconds: 25 };
    return json({ plan, tier }, 200, "cloudflare-plan");
  }

  if (method === "GET" && cleanPath === "pricing") {
    return json(
      {
        billing_enabled: billing.enabled,
        tiers: {
          free: { billing: { monthly_configured: false, annual_configured: false } },
          premium: { billing: { monthly_configured: !!billing.premium[0], annual_configured: !!billing.premiumAnnual[0] } },
          vip: { billing: { monthly_configured: !!billing.vip[0], annual_configured: !!billing.vipAnnual[0] } },
        },
      },
      200,
      "cloudflare-pricing",
    );
  }

  if (method === "GET" && cleanPath === "trial/status") {
    let trialUntil = Number(user.trialUntil || 0);
    let claimed = !!user.trialClaimed;
    if (billing.enabled && userId) {
      try {
        const customer = await stripeFindCustomerByUserId(env, userId);
        const sub = await stripeActiveSubscription(env, customer?.id || "");
        const status = String(sub?.status || "").toLowerCase();
        const trialEnd = Number(sub?.trial_end || 0);
        if (status === "trialing" && trialEnd > nowSec) {
          trialUntil = Math.max(trialUntil, trialEnd);
          claimed = true;
          userState.set(userId, { ...user, trialUntil, trialClaimed: true });
        }
      } catch (_) {}
    }
    const secs = Math.max(0, trialUntil - nowSec);
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
    return json({ ok: true, granted_hours: 72, trial_plan: PLAN_PREMIUM, trial_until: trialUntil }, 200, "cloudflare-trial");
  }

  if (method === "POST" && cleanPath === "billing/checkout") {
    if (!billing.enabled) return json({ detail: "Billing is temporarily disabled." }, 503, "cloudflare-billing");
    if (!userId) return json({ detail: "Authenticated user required." }, 401, "cloudflare-billing");
    const body = await request.json().catch(() => ({}));
    const tier = normalizePlan(body?.tier || "");
    if (tier === PLAN_FREE) return json({ detail: "Free plan does not require checkout." }, 400, "cloudflare-billing");
    let cycle = String(body?.billing_cycle || "monthly").toLowerCase() === "annual" ? "annual" : "monthly";
    let priceId = priceIdForTierCycle(billing, tier, cycle);
    if (!priceId && cycle === "annual") {
      cycle = "monthly";
      priceId = priceIdForTierCycle(billing, tier, cycle);
    }
    if (!priceId) return json({ detail: "Checkout is not configured for this plan." }, 400, "cloudflare-billing");

    const successUrl = String(body?.success_url || `${defaultReturnBase(env)}?checkout=success`);
    const cancelUrl = String(body?.cancel_url || `${defaultReturnBase(env)}?checkout=cancel`);
    if (!isAllowedReturnUrl(successUrl, env) || !isAllowedReturnUrl(cancelUrl, env)) {
      return json({ detail: "Invalid checkout redirect URL." }, 400, "cloudflare-billing");
    }

    try {
      const customer = await stripeFindOrCreateCustomer(env, userId);
      const form = {
        mode: "subscription",
        customer: String(customer.id),
        success_url: successUrl,
        cancel_url: cancelUrl,
        allow_promotion_codes: "true",
        "line_items[0][price]": priceId,
        "line_items[0][quantity]": "1",
        "metadata[userId]": userId,
        "metadata[tier]": tier,
        "metadata[billing_cycle]": cycle,
      };
      const trialRequested = !!body?.trial_days && Number(body?.trial_days || 0) > 0 && tier === PLAN_PREMIUM;
      if (trialRequested) {
        const td = Math.max(1, Math.min(7, Number(body?.trial_days || 3)));
        form["subscription_data[trial_period_days]"] = String(td);
        form["subscription_data[metadata][userId]"] = userId;
        form["subscription_data[metadata][trial_mode]"] = String(body?.trial_mode || "card_required_auto_charge");
        form["payment_method_collection"] = "always";
      }
      const session = await stripeRequest(env, "/v1/checkout/sessions", "POST", form);
      return json({ checkout_url: session?.url || "", session_id: session?.id || "", billing_cycle: cycle, tier }, 200, "cloudflare-billing");
    } catch (err) {
      return json({ detail: String(err?.message || "Checkout unavailable") }, Number(err?.status || 500), "cloudflare-billing");
    }
  }

  if (method === "POST" && cleanPath === "billing/webhook") {
    try {
      const rawBody = await request.text();
      const verified = await verifyStripeWebhookSignature(env, request, rawBody);
      if (!verified.ok) return json({ detail: verified.detail }, 400, "cloudflare-billing");
      const event = JSON.parse(rawBody || "{}");
      const eventType = String(event?.type || "");
      const obj = event?.data?.object || {};
      let userIdFromEvent = normalizeUserId(obj?.metadata?.userId || obj?.metadata?.user_id || "");

      if (!userIdFromEvent && obj?.customer) {
        try {
          const customer = await stripeRequest(env, `/v1/customers/${encodeURIComponent(String(obj.customer))}`, "GET");
          userIdFromEvent = normalizeUserId(customer?.metadata?.userId || "");
        } catch (_) {}
      }

      if (userIdFromEvent) {
        const existing = userState.get(userIdFromEvent) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };

        if (eventType.startsWith("customer.subscription.")) {
          const status = String(obj?.status || "").toLowerCase();
          const nextPlan = ACTIVE_SUB_STATUSES.has(status) ? subscriptionPlan(billing, obj) : PLAN_FREE;
          const trialEnd = Number(obj?.trial_end || 0);
          userState.set(userIdFromEvent, {
            ...existing,
            plan: nextPlan,
            trialUntil: trialEnd > nowSec ? trialEnd : existing.trialUntil,
            trialClaimed: existing.trialClaimed || trialEnd > 0,
          });
        }

        if (eventType === "checkout.session.completed") {
          const subId = String(obj?.subscription || "").trim();
          let nextPlan = existing.plan;
          let trialEnd = existing.trialUntil;
          if (subId) {
            try {
              const sub = await stripeRequest(env, `/v1/subscriptions/${encodeURIComponent(subId)}`, "GET");
              nextPlan = subscriptionPlan(billing, sub);
              const te = Number(sub?.trial_end || 0);
              if (te > nowSec) trialEnd = te;
            } catch (_) {}
          } else {
            nextPlan = normalizePlan(obj?.metadata?.tier || PLAN_PREMIUM);
          }
          userState.set(userIdFromEvent, {
            ...existing,
            plan: nextPlan,
            trialUntil: trialEnd,
            trialClaimed: existing.trialClaimed || trialEnd > 0,
          });
        }
      }

      return json({ received: true }, 200, "cloudflare-billing");
    } catch (err) {
      return json({ detail: String(err?.message || "Webhook processing failed.") }, 400, "cloudflare-billing");
    }
  }

  if (method === "POST" && cleanPath === "billing/portal") {
    if (!billing.enabled) return json({ detail: "Billing is temporarily disabled." }, 503, "cloudflare-billing");
    if (!userId) return json({ detail: "Authenticated user required." }, 401, "cloudflare-billing");
    const body = await request.json().catch(() => ({}));
    const returnUrl = String(body?.return_url || defaultReturnBase(env));
    if (!isAllowedReturnUrl(returnUrl, env)) return json({ detail: "Invalid portal return URL." }, 400, "cloudflare-billing");
    try {
      const customer = await stripeFindOrCreateCustomer(env, userId);
      const session = await stripeRequest(env, "/v1/billing_portal/sessions", "POST", {
        customer: String(customer.id),
        return_url: returnUrl,
      });
      return json({ portal_url: session?.url || "" }, 200, "cloudflare-billing");
    } catch (err) {
      return json({ detail: String(err?.message || "Billing portal unavailable") }, Number(err?.status || 500), "cloudflare-billing");
    }
  }

  if (method === "POST" && cleanPath === "billing/cancel-trial") {
    if (!billing.enabled) return json({ detail: "Billing is temporarily disabled." }, 503, "cloudflare-billing");
    if (!userId) return json({ detail: "Authenticated user required." }, 401, "cloudflare-billing");
    try {
      const customer = await stripeFindCustomerByUserId(env, userId);
      if (!customer?.id) return json({ detail: "No active subscription found." }, 404, "cloudflare-billing");
      const sub = await stripeActiveSubscription(env, customer.id);
      if (!sub?.id) return json({ detail: "No active subscription found." }, 404, "cloudflare-billing");
      const updated = await stripeRequest(env, `/v1/subscriptions/${encodeURIComponent(sub.id)}`, "POST", {
        cancel_at_period_end: "true",
      });
      const trialEnd = Number(updated?.trial_end || 0);
      if (trialEnd > nowSec && userId) userState.set(userId, { ...user, trialUntil: trialEnd, trialClaimed: true });
      return json(
        {
          ok: true,
          subscription_id: updated?.id || sub.id,
          status: updated?.status || sub.status,
          cancel_at_period_end: !!updated?.cancel_at_period_end,
          trial_end: trialEnd || null,
        },
        200,
        "cloudflare-billing",
      );
    } catch (err) {
      return json({ detail: String(err?.message || "Could not cancel trial.") }, Number(err?.status || 500), "cloudflare-billing");
    }
  }

  const url = toUpstreamUrl(request.url, rawPath);
  const hasApiKey = !!String(request.headers.get("x-api-key") || "").trim();
  const resolvedUserPlan = await resolveEffectivePlanForUser(env, userId, user, nowSec, clientPlanHint);
  const init = {
    method,
    headers: forwardHeaders(request),
    redirect: "follow",
  };
  const proxyApiKey = String(env?.BACKEND_API_KEY || "").trim();
  if (!hasApiKey && proxyApiKey) init.headers.set("x-api-key", proxyApiKey);
  if (userId) init.headers.set("x-user-plan", normalizePlan(resolvedUserPlan));
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
