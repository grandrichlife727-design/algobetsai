const FEED_CACHE = {
  ts: 0,
  rows: [],
};

const MANUAL_SPLITS = new Map();
const MANUAL_TTL_MS = 6 * 60 * 60 * 1000;

function safeNum(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function normalizeKey(sport, game, side = "") {
  const s = String(sport || "").trim().toLowerCase();
  const g = String(game || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/\bat\b/g, "@");
  const sd = String(side || "").trim().toLowerCase();
  return `${s}|${g}|${sd}`;
}

function cleanupManual() {
  const now = Date.now();
  for (const [k, v] of MANUAL_SPLITS.entries()) {
    if ((now - Number(v.ts || 0)) > MANUAL_TTL_MS) MANUAL_SPLITS.delete(k);
  }
}

function normalizeFeedRow(raw) {
  const sport = String(raw?.sport || raw?.sport_key || "").trim().toLowerCase();
  const game = String(raw?.game || raw?.event || raw?.matchup || "").trim();
  const side = String(raw?.side || raw?.team || "").trim();
  const publicPct = safeNum(raw?.public_pct ?? raw?.publicPct ?? raw?.tickets_pct ?? raw?.ticket_pct);
  const sharpPct = safeNum(raw?.sharp_pct ?? raw?.sharpPct ?? raw?.money_pct ?? raw?.handle_pct);
  if (!sport || !game || !Number.isFinite(publicPct) || !Number.isFinite(sharpPct)) return null;
  return {
    sport,
    game,
    side,
    publicPct: Math.max(0, Math.min(100, publicPct)),
    sharpPct: Math.max(0, Math.min(100, sharpPct)),
    ts: Date.now(),
  };
}

async function fetchProviderFeed(env) {
  const url = String(env?.TICKET_SPLITS_FEED_URL || "").trim();
  if (!url) return [];
  const headers = { Accept: "application/json" };
  const bearer = String(env?.TICKET_SPLITS_FEED_KEY || "").trim();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  const res = await fetch(url, { method: "GET", headers });
  if (!res.ok) throw new Error(`Splits feed failed (${res.status})`);
  const body = await res.json();
  const arr = Array.isArray(body) ? body : (Array.isArray(body?.rows) ? body.rows : []);
  return arr.map(normalizeFeedRow).filter(Boolean);
}

export async function loadTicketSplits(env) {
  cleanupManual();
  const refreshSec = Math.max(30, Number(env?.TICKET_SPLITS_FEED_REFRESH_SECONDS || 300));
  const now = Date.now();
  if ((now - FEED_CACHE.ts) > refreshSec * 1000) {
    try {
      FEED_CACHE.rows = await fetchProviderFeed(env);
      FEED_CACHE.ts = now;
    } catch (_) {
      FEED_CACHE.ts = now;
    }
  }
  const merged = [...FEED_CACHE.rows];
  for (const v of MANUAL_SPLITS.values()) merged.push(v);
  return merged;
}

export function importTicketSplits(rows) {
  cleanupManual();
  const arr = Array.isArray(rows) ? rows : [];
  let upserted = 0;
  for (const raw of arr) {
    const n = normalizeFeedRow(raw);
    if (!n) continue;
    const k = normalizeKey(n.sport, n.game, n.side);
    MANUAL_SPLITS.set(k, n);
    upserted += 1;
  }
  return { upserted, size: MANUAL_SPLITS.size };
}

export function attachSplitsToPicks(picks, splits) {
  const index = new Map();
  for (const row of Array.isArray(splits) ? splits : []) {
    const base = normalizeKey(row.sport, row.game, "");
    const bySide = normalizeKey(row.sport, row.game, row.side);
    index.set(bySide, row);
    if (!index.has(base)) index.set(base, row);
  }

  return (Array.isArray(picks) ? picks : []).map((p) => {
    const sport = String(p?.sport || "").toLowerCase();
    const game = String(p?.game || "");
    const side = String(p?.bet || "").replace(/\s*ML\s*$/i, "").trim();
    const hit = index.get(normalizeKey(sport, game, side)) || index.get(normalizeKey(sport, game, ""));
    if (!hit) return p;
    return {
      ...p,
      publicPct: Number(hit.publicPct),
      sharpPct: Number(hit.sharpPct),
      public_pct: Number(hit.publicPct),
      sharp_pct: Number(hit.sharpPct),
      splits_source: "ticket_feed",
    };
  });
}
