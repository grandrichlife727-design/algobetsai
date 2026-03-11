import { evFinderRows, getScanPayload } from "../_lib/odds-engine.js";
import { importTicketSplits, loadTicketSplits } from "../_lib/ticket-splits.js";

const UPSTREAM = "https://algobetsai.onrender.com";
const STRIPE_BASE = "https://api.stripe.com";
const PLAN_FREE = "free";
const PLAN_PREMIUM = "premium";
const PLAN_VIP = "vip";
const PROPS_CACHE_VERSION = "v2";
const TELEMETRY_CACHE_VERSION = "v1";
const TELEMETRY_MAX_DAYS = 30;
const TELEMETRY_UNIQUE_CAP = 4000;
const AUTH_TTL_SECONDS = 30 * 24 * 3600;
const EMAIL_VERIFY_TTL_SECONDS = 15 * 60;
const EMAIL_VERIFY_VERIFIED_TTL_SECONDS = 365 * 24 * 3600;
const TRACKED_PICKS_MAX = 200;
const ACTIVE_SUB_STATUSES = new Set(["active", "trialing", "past_due", "unpaid"]);
const userState = new Map();
const emailVerifyState = new Map();
const ODDS_BASE = "https://api.the-odds-api.com/v4";
const BDL_BASE = "https://api.balldontlie.io/v1";
const PROPS_MARKETS_BY_SPORT = {
  basketball_nba: ["player_points", "player_rebounds"],
  americanfootball_nfl: ["player_pass_yds", "player_rush_yds"],
};
const nbaPlayerCache = new Map();
const nbaPlayerStatsCache = new Map();
const DEFAULT_PREMIUM_OVERRIDE_EMAILS = new Set(["grandrichlife727@gmail.com"]);
const DEFAULT_VIP_OVERRIDE_EMAILS = new Set(["rosegoldwilly@gmail.com"]);
const FUNNEL_UNIQUE_EVENTS = new Set([
  "auth_modal_opened",
  "auth_continue_clicked",
  "auth_completed",
  "upgrade_picker_opened",
  "upgrade_auth_gate_shown",
  "upgrade_auth_gate_click",
  "upgrade_plan_selected",
  "checkout_started",
  "checkout_success",
]);
const SCORE_SPORT_MAP = {
  nba: "basketball_nba",
  basketball_nba: "basketball_nba",
  nfl: "americanfootball_nfl",
  americanfootball_nfl: "americanfootball_nfl",
  nhl: "icehockey_nhl",
  icehockey_nhl: "icehockey_nhl",
  ncaab: "basketball_ncaab",
  basketball_ncaab: "basketball_ncaab",
  mlb: "baseball_mlb",
  baseball_mlb: "baseball_mlb",
  epl: "soccer_epl",
  soccer_epl: "soccer_epl",
  "la liga": "soccer_spain_la_liga",
  soccer_spain_la_liga: "soccer_spain_la_liga",
};

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

function normalizeEmail(value) {
  return String(value || "").trim().toLowerCase();
}

function emailVerifyKey(email) {
  return normalizeEmail(email);
}

function generateEmailCode() {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return String((buf[0] % 900000) + 100000);
}

function trackedKvKey(userId) {
  return `tracked:${String(userId || "").trim().toLowerCase()}`;
}

function communityPostsKey() {
  return "community:posts";
}

function communityRateKey(userId, dateKey) {
  return `community:rate:${String(userId || "").trim().toLowerCase()}:${String(dateKey || "")}`;
}

function trackedStatusRank(status) {
  const s = String(status || "").toLowerCase();
  if (s === "win" || s === "loss" || s === "push") return 3;
  if (s === "open") return 2;
  if (s === "pending") return 1;
  return 0;
}

function parseOddsValue(raw) {
  const txt = String(raw || "").trim();
  if (!txt) return null;
  const n = Number(txt.replace(/[^\d\-\+\.]/g, ""));
  return Number.isFinite(n) ? n : null;
}

function profitFromOdds(odds, stake) {
  const n = Number(odds || 0);
  const s = Number(stake || 1);
  if (!Number.isFinite(n) || !Number.isFinite(s)) return 0;
  if (n > 0) return (n / 100) * s;
  if (n < 0) return (100 / Math.abs(n)) * s;
  return 0;
}

function summarizeTrackedPicks(picks, days = 7) {
  const cutoff = Date.now() - (days * 24 * 60 * 60 * 1000);
  const rows = (Array.isArray(picks) ? picks : []).filter((p) => Number(p?.ts || 0) >= cutoff);
  let wins = 0;
  let losses = 0;
  let pushes = 0;
  let pending = 0;
  let units = 0;
  let staked = 0;
  let evSum = 0;
  let evCount = 0;
  rows.forEach((p) => {
    const status = String(p?.status || "").toLowerCase();
    if (status === "win") wins += 1;
    else if (status === "loss") losses += 1;
    else if (status === "push") pushes += 1;
    else pending += 1;
    const stake = Number(p?.units || p?.stake || 1);
    if (status === "win") {
      units += profitFromOdds(parseOddsValue(p?.odds), stake);
      staked += stake;
    } else if (status === "loss") {
      units -= stake;
      staked += stake;
    } else if (status === "push") {
      staked += 0;
    }
    const ev = Number(p?.ev || 0);
    if (Number.isFinite(ev) && ev !== 0) {
      evSum += ev;
      evCount += 1;
    }
  });
  const settled = wins + losses + pushes;
  const winPct = settled ? (wins / settled) * 100 : 0;
  const roiPct = staked ? (units / staked) * 100 : 0;
  return {
    window_days: days,
    total: rows.length,
    settled,
    wins,
    losses,
    pushes,
    pending,
    win_pct: Number(winPct.toFixed(1)),
    roi_pct: Number(roiPct.toFixed(1)),
    units: Number(units.toFixed(2)),
    avg_edge: evCount ? Number((evSum / evCount).toFixed(2)) : 0,
  };
}

function normalizeTsSeconds(ts) {
  const n = Number(ts || 0);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n > 2_000_000_000 ? Math.floor(n / 1000) : Math.floor(n);
}

function marketFromBetText(bet, explicit) {
  const raw = String(explicit || "").trim().toLowerCase();
  const norm = raw.replace(/\s+/g, "_").replace(/-/g, "_");
  if (["ml", "moneyline", "money_line"].includes(norm)) return "moneyline";
  if (["spread", "spreads", "point_spread", "handicap"].includes(norm)) return "spread";
  if (["total", "totals", "over_under", "ou"].includes(norm)) return "total";
  if (norm.includes("prop") || norm.includes("player_") || norm.includes("points") || norm.includes("rebounds") || norm.includes("assists") || norm.includes("yards")) return "props";
  const b = String(bet || "").toLowerCase();
  if (/\b(over|under|o|u)\b/.test(b) && /(\d+(\.\d+)?)/.test(b)) return "total";
  if (/\b(ml|moneyline|money line)\b/.test(b)) return "moneyline";
  if (/\b(points|rebounds|assists|yards|prop)\b/.test(b)) return "props";
  if (/[+-]\d+(\.\d+)?/.test(b)) return "spread";
  return "spread";
}

function walkForwardMetricsFromRows(rows, weeks = 12) {
  const now = new Date();
  const windowWeeks = Math.max(2, Math.min(52, Number(weeks || 12)));
  const weekRows = [];
  const bySport = {};
  const byMarket = {};
  const profitForRow = (r) => {
    const status = String(r?.status || "").toLowerCase();
    if (status === "win") return profitFromOdds(parseOddsValue(r?.odds), 1);
    if (status === "loss") return -1;
    return 0;
  };
  for (let w = 0; w < windowWeeks; w += 1) {
    const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - ((w + 1) * 7)));
    const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - (w * 7)));
    const bucket = [];
    for (const r of rows) {
      const ts = normalizeTsSeconds(r?.settled_ts || r?.ts || 0);
      if (!ts) continue;
      const dt = new Date(ts * 1000);
      if (dt >= start && dt < end) bucket.push(r);
    }
    const wins = bucket.filter(r => String(r?.status || "").toLowerCase() === "win").length;
    const losses = bucket.filter(r => String(r?.status || "").toLowerCase() === "loss").length;
    const pushes = bucket.filter(r => String(r?.status || "").toLowerCase() === "push").length;
    const graded = wins + losses + pushes;
    const units = Number(bucket.reduce((s, r) => s + profitForRow(r), 0).toFixed(2));
    const risked = Math.max(1, wins + losses);
    const winPct = wins + losses ? Number(((wins / (wins + losses)) * 100).toFixed(1)) : 0;
    const roiPct = wins + losses ? Number(((units / risked) * 100).toFixed(1)) : 0;
    const week_start = start.toISOString().slice(0, 10);
    const week_end = end.toISOString().slice(0, 10);
    weekRows.push({
      week_start,
      week_end,
      week_range: `${week_start}→${week_end}`,
      graded_picks: graded,
      wins,
      losses,
      units,
      win_pct: winPct,
      roi_pct: roiPct,
    });
  }

  function acc(map, row) {
    const status = String(row?.status || "").toLowerCase();
    map.graded = Number(map.graded || 0) + 1;
    if (status === "win") map.wins = Number(map.wins || 0) + 1;
    else if (status === "loss") map.losses = Number(map.losses || 0) + 1;
    map.units = Number(map.units || 0) + profitForRow(row);
    map.ev_sum = Number(map.ev_sum || 0) + Number(row?.ev || row?.edge || 0);
    map.ev_n = Number(map.ev_n || 0) + 1;
  }

  const settled = rows.filter(r => ["win", "loss", "push"].includes(String(r?.status || "").toLowerCase()));
  for (const r of settled) {
    const sport = String(r?.sport || r?.sport_key || "unknown");
    const market = marketFromBetText(r?.bet, r?.bet_type || r?.market);
    if (!bySport[sport]) bySport[sport] = {};
    if (!byMarket[market]) byMarket[market] = {};
    acc(bySport[sport], r);
    acc(byMarket[market], r);
  }

  function finalize(group) {
    const out = {};
    Object.entries(group || {}).forEach(([k, v]) => {
      const wins = Number(v.wins || 0);
      const losses = Number(v.losses || 0);
      const risked = Math.max(1, wins + losses);
      const units = Number(Number(v.units || 0).toFixed(2));
      out[k] = {
        graded_picks: Number(v.graded || 0),
        wins,
        losses,
        win_pct: wins + losses ? Number(((wins / (wins + losses)) * 100).toFixed(1)) : 0,
        units,
        roi_pct: wins + losses ? Number(((units / risked) * 100).toFixed(1)) : 0,
        avg_ev: Number((Number(v.ev_sum || 0) / Math.max(1, Number(v.ev_n || 0))).toFixed(2)),
      };
    });
    return out;
  }

  return {
    weeks: weekRows.reverse(),
    by_sport: finalize(bySport),
    by_market: finalize(byMarket),
  };
}

function chooseBetterTrackedPick(a, b) {
  if (!a) return b;
  if (!b) return a;
  const ra = trackedStatusRank(a.status);
  const rb = trackedStatusRank(b.status);
  if (ra !== rb) return rb > ra ? b : a;
  const ta = Number(a.ts || 0);
  const tb = Number(b.ts || 0);
  if (tb !== ta) return tb > ta ? b : a;
  return b;
}

function mergeTrackedPicks(existing, incoming) {
  const bySig = new Map();
  const extras = [];
  const add = (p) => {
    if (!p || typeof p !== "object") return;
    const sig = String(p.signature || "").trim();
    if (!sig) {
      extras.push(p);
      return;
    }
    const cur = bySig.get(sig);
    bySig.set(sig, chooseBetterTrackedPick(cur, p));
  };
  (Array.isArray(existing) ? existing : []).forEach(add);
  (Array.isArray(incoming) ? incoming : []).forEach(add);
  return [...bySig.values(), ...extras]
    .sort((a, b) => Number(b?.ts || 0) - Number(a?.ts || 0))
    .slice(0, TRACKED_PICKS_MAX);
}

function normalizeTrackedPicksInput(picks) {
  return (Array.isArray(picks) ? picks : [])
    .filter((p) => p && typeof p === "object")
    .map((p) => ({
      ...p,
      signature: String(p.signature || "").trim(),
      status: String(p.status || "").trim(),
      ts: Number(p.ts || 0),
    }))
    .slice(0, TRACKED_PICKS_MAX);
}

async function readTrackedFromKv(env, userId) {
  if (!env?.ALGOBETS_TRACKED || !userId) return null;
  const raw = await env.ALGOBETS_TRACKED.get(trackedKvKey(userId));
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

async function readCommunityPosts(env) {
  if (!env?.ALGOBETS_COMMUNITY) return [];
  const raw = await env.ALGOBETS_COMMUNITY.get(communityPostsKey());
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

async function writeCommunityPosts(env, posts) {
  if (!env?.ALGOBETS_COMMUNITY) return false;
  const payload = JSON.stringify(Array.isArray(posts) ? posts.slice(0, 500) : []);
  await env.ALGOBETS_COMMUNITY.put(communityPostsKey(), payload);
  return true;
}

async function writeTrackedToKv(env, userId, picks) {
  if (!env?.ALGOBETS_TRACKED || !userId) return false;
  const payload = JSON.stringify(normalizeTrackedPicksInput(picks));
  await env.ALGOBETS_TRACKED.put(trackedKvKey(userId), payload);
  return true;
}

async function getEmailVerifyRecord(requestUrl, key) {
  const rec = emailVerifyState.get(key);
  if (rec && (!rec.expires_ts || Date.now() <= rec.expires_ts)) return rec;
  const cached = await readEmailVerify(requestUrl, key);
  if (cached) emailVerifyState.set(key, cached);
  return cached;
}

async function setEmailVerifyRecord(requestUrl, key, record) {
  emailVerifyState.set(key, record);
  await writeEmailVerify(requestUrl, key, record);
  return record;
}

async function sendVerificationEmail(env, to, code) {
  const resendKey = String(env?.RESEND_API_KEY || "").trim();
  const resendFrom = String(env?.RESEND_FROM_EMAIL || env?.MAIL_FROM || "").trim();
  if (resendKey && resendFrom) {
    const payload = {
      from: resendFrom,
      to: [to],
      subject: "Your AlgoBets Ai verification code",
      text: `Your AlgoBets Ai verification code is ${code}. It expires in 15 minutes.`,
      html: `<p>Your AlgoBets Ai verification code is <strong>${code}</strong>.</p><p>It expires in 15 minutes.</p>`,
    };
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        authorization: `Bearer ${resendKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || "Email provider rejected the request.");
    }
    return;
  }
  const apiKey = String(env?.SENDGRID_API_KEY || "").trim();
  const from = String(env?.SENDGRID_FROM_EMAIL || env?.MAIL_FROM || "").trim();
  if (!apiKey || !from) throw new Error("Email provider not configured.");
  const payload = {
    personalizations: [{ to: [{ email: to }], subject: "Your AlgoBets Ai verification code" }],
    from: { email: from, name: "AlgoBets Ai" },
    content: [
      { type: "text/plain", value: `Your AlgoBets Ai verification code is ${code}. It expires in 15 minutes.` },
      { type: "text/html", value: `<p>Your AlgoBets Ai verification code is <strong>${code}</strong>.</p><p>It expires in 15 minutes.</p>` },
    ],
  };
  const res = await fetch("https://api.sendgrid.com/v3/mail/send", {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || "Email provider rejected the request.");
  }
}

function sportsDbKey(env) {
  const key = String(env?.THE_SPORTS_DB_KEY || env?.SPORTSDB_KEY || "").trim();
  return key || "123";
}

function normalizeSportsDbSport(rawSport = "", rawLeague = "") {
  const sport = String(rawSport || "").toLowerCase();
  const league = String(rawLeague || "").toLowerCase();
  if (sport === "basketball") {
    if (league.includes("nba")) return "basketball_nba";
    if (league.includes("ncaa") || league.includes("college")) return "basketball_ncaab";
  }
  if (sport === "ice hockey" || sport === "icehockey") {
    if (league.includes("nhl")) return "icehockey_nhl";
  }
  if (sport === "baseball") {
    if (league.includes("mlb")) return "baseball_mlb";
  }
  if (sport === "soccer") {
    if (league.includes("premier league")) return "soccer_epl";
    if (league.includes("la liga")) return "soccer_spain_la_liga";
  }
  if (sport === "american football" || sport === "americanfootball") {
    if (league.includes("nfl")) return "americanfootball_nfl";
  }
  if (sport === "mma" || sport === "mixed martial arts") {
    return "mma_mixed_martial_arts";
  }
  return "";
}

function sportsDbEventToGame(evt) {
  const sportKey = normalizeSportsDbSport(evt?.strSport, evt?.strLeague);
  if (!sportKey) return null;
  const date = String(evt?.dateEvent || "").trim();
  const time = String(evt?.strTime || "00:00:00").trim();
  const iso = date ? `${date}T${time}${time.endsWith("Z") ? "" : "Z"}` : "";
  const home = String(evt?.strHomeTeam || "").trim();
  const away = String(evt?.strAwayTeam || "").trim();
  if (!home || !away) return null;
  return {
    sport: sportKey,
    label: evt?.strLeague || evt?.strSport || "",
    home_team: home,
    away_team: away,
    game_time: iso,
    home_ml: null,
    away_ml: null,
  };
}

async function fetchSportsDbDay(env, dateStr) {
  const key = sportsDbKey(env);
  const url = `https://www.thesportsdb.com/api/v1/json/${key}/eventsday.php?d=${encodeURIComponent(dateStr)}`;
  const res = await fetch(url, { method: "GET" });
  if (!res.ok) return [];
  const payload = await res.json().catch(() => ({}));
  const rows = Array.isArray(payload?.events) ? payload.events : [];
  return rows.map(sportsDbEventToGame).filter(Boolean);
}

function premiumOverrideEmails(env) {
  const merged = new Set(DEFAULT_PREMIUM_OVERRIDE_EMAILS);
  for (const email of splitCsvEnv(env?.PREMIUM_OVERRIDE_EMAILS)) {
    merged.add(String(email || "").trim().toLowerCase());
  }
  return merged;
}

function vipOverrideEmails(env) {
  const merged = new Set(DEFAULT_VIP_OVERRIDE_EMAILS);
  for (const email of splitCsvEnv(env?.VIP_OVERRIDE_EMAILS)) {
    merged.add(String(email || "").trim().toLowerCase());
  }
  return merged;
}

function isPremiumOverrideEmail(env, value) {
  const email = String(value || "").trim().toLowerCase();
  return !!email && premiumOverrideEmails(env).has(email);
}

function isVipOverrideEmail(env, value) {
  const email = String(value || "").trim().toLowerCase();
  return !!email && vipOverrideEmails(env).has(email);
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

function clampNumber(value, min, max) {
  const num = Number(value);
  if (!Number.isFinite(num)) return min;
  return Math.max(min, Math.min(max, num));
}

function resolveBalldontlieKey(env) {
  return String(env?.BALLDONTLIE_API_KEY || env?.BDL_API_KEY || "").trim();
}

function normalizePlayerName(raw = "") {
  return String(raw || "")
    .toLowerCase()
    .replace(/[^a-z\s]/g, " ")
    .replace(/\b(jr|sr|ii|iii|iv|v)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function nbaSeasonYear(now = new Date()) {
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth() + 1;
  return month >= 8 ? year : year - 1;
}

async function fetchBalldontliePlayersByName(apiKey, name, perPage = 5) {
  const url = `${BDL_BASE}/players?search=${encodeURIComponent(name)}&per_page=${perPage}`;
  const res = await fetch(url, {
    method: "GET",
    headers: { authorization: apiKey },
  });
  if (!res.ok) return [];
  const payload = await res.json().catch(() => ({}));
  return Array.isArray(payload?.data) ? payload.data : [];
}

async function resolveBalldontliePlayerId(apiKey, name, ttlMs = 7 * 24 * 3600 * 1000) {
  const key = normalizePlayerName(name);
  if (!key) return null;
  const cached = nbaPlayerCache.get(key);
  if (cached && Date.now() - cached.ts < ttlMs) return cached.id;
  const candidates = await fetchBalldontliePlayersByName(apiKey, name, 8);
  if (!candidates.length) return null;
  const normalized = normalizePlayerName(name);
  let best = candidates[0];
  for (const c of candidates) {
    const full = normalizePlayerName(`${c.first_name || ""} ${c.last_name || ""}`);
    if (full === normalized) {
      best = c;
      break;
    }
  }
  const id = best?.id || null;
  if (id) nbaPlayerCache.set(key, { id, ts: Date.now() });
  return id;
}

async function fetchBalldontlieStats(apiKey, playerId, season, perPage = 8) {
  const url = `${BDL_BASE}/stats?player_ids[]=${encodeURIComponent(playerId)}&seasons[]=${encodeURIComponent(season)}&per_page=${perPage}&postseason=false`;
  const res = await fetch(url, {
    method: "GET",
    headers: { authorization: apiKey },
  });
  if (!res.ok) return [];
  const payload = await res.json().catch(() => ({}));
  return Array.isArray(payload?.data) ? payload.data : [];
}

async function fetchPlayerAverages(apiKey, playerId, season, games, ttlMs = 4 * 3600 * 1000) {
  const cacheKey = `${playerId}:${season}:${games}`;
  const cached = nbaPlayerStatsCache.get(cacheKey);
  if (cached && Date.now() - cached.ts < ttlMs) return cached.data;
  const rows = await fetchBalldontlieStats(apiKey, playerId, season, Math.max(5, games));
  if (!rows.length) return null;
  rows.sort((a, b) => String(b?.game?.date || "").localeCompare(String(a?.game?.date || "")));
  const slice = rows.slice(0, games);
  let pts = 0;
  let reb = 0;
  let count = 0;
  for (const r of slice) {
    if (!Number.isFinite(r?.pts) || !Number.isFinite(r?.reb)) continue;
    pts += Number(r.pts);
    reb += Number(r.reb);
    count += 1;
  }
  if (!count) return null;
  const data = {
    pts: pts / count,
    reb: reb / count,
    games: count,
  };
  nbaPlayerStatsCache.set(cacheKey, { data, ts: Date.now() });
  return data;
}

function americanToImplied(odds) {
  const o = Number(odds);
  if (!Number.isFinite(o) || o === 0) return null;
  if (o > 0) return 100 / (o + 100);
  return Math.abs(o) / (Math.abs(o) + 100);
}

function impliedToAmerican(prob) {
  const p = Number(prob);
  if (!Number.isFinite(p) || p <= 0 || p >= 1) return null;
  if (p >= 0.5) return Math.round(-((p * 100) / (1 - p)));
  return Math.round(((1 - p) * 100) / p);
}

function medianNumber(list = []) {
  const arr = list.filter((n) => Number.isFinite(n)).sort((a, b) => a - b);
  if (!arr.length) return null;
  const mid = Math.floor(arr.length / 2);
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

async function applyNbaPropModel(recs, options = {}) {
  const apiKey = String(options.apiKey || "").trim();
  if (!apiKey) return { props: [], used_model: false, detail: "Missing NBA model key." };
  const minModelEdge = Number.isFinite(options.minModelEdgePct) ? options.minModelEdgePct : 4;
  const games = clampNumber(options.games || 8, 5, 12);
  const maxPlayers = clampNumber(options.maxPlayers || 12, 6, 25);
  const weight = Number.isFinite(options.modelWeight) ? options.modelWeight : 0.6;
  const season = Number.isFinite(options.season) ? options.season : nbaSeasonYear();
  const nameQueue = [];
  const seen = new Set();
  for (const r of recs) {
    const key = normalizePlayerName(r?.player || "");
    if (!key || seen.has(key)) continue;
    seen.add(key);
    nameQueue.push({ key, name: r.player });
    if (nameQueue.length >= maxPlayers) break;
  }
  const projections = new Map();
  for (const item of nameQueue) {
    const playerId = await resolveBalldontliePlayerId(apiKey, item.name);
    if (!playerId) continue;
    const stats = await fetchPlayerAverages(apiKey, playerId, season, games);
    if (!stats) continue;
    projections.set(item.key, stats);
  }
  if (!projections.size) return { props: [], used_model: true, detail: "No model matches." };
  const enriched = [];
  for (const r of recs) {
    const key = normalizePlayerName(r?.player || "");
    const stats = projections.get(key);
    if (!stats) continue;
    const market = String(r.market || "");
    const line = Number(r.line);
    if (!Number.isFinite(line) || line <= 0) continue;
    let proj = null;
    if (market === "player_points") proj = stats.pts;
    if (market === "player_rebounds") proj = stats.reb;
    if (!Number.isFinite(proj)) continue;
    const side = String(r.side || "").toLowerCase();
    const diff = side.includes("under") ? (line - proj) : (proj - line);
    const modelEdgePct = (diff / line) * 100;
    if (!Number.isFinite(modelEdgePct) || modelEdgePct < minModelEdge) continue;
    const score = modelEdgePct * weight + Number(r.edge || 0) * (1 - weight);
    enriched.push({
      ...r,
      model_proj: Math.round(proj * 10) / 10,
      model_edge: Math.round(modelEdgePct * 10) / 10,
      model_games: stats.games,
      score: Math.round(score * 10) / 10,
      model_source: "balldontlie_recent_avg",
    });
  }
  enriched.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  return { props: enriched, used_model: true, detail: "ok" };
}

function recommendProps(rawProps = [], options = {}) {
  const minEdgePct = Number.isFinite(options.minEdgePct) ? options.minEdgePct : 1.5;
  const minBooks = Number.isFinite(options.minBooks) ? options.minBooks : 3;
  const maxRecs = clampNumber(options.maxRecs || 12, 5, 30);
  const groups = new Map();
  for (const p of rawProps) {
    const player = String(p?.player || "").trim();
    const side = String(p?.side || "").trim();
    const market = String(p?.market || "").trim();
    const line = p?.line;
    const odds = p?.odds;
    if (!player || !side || !market || line == null || odds == null) continue;
    const key = `${player}|${market}|${side}|${line}`;
    const implied = americanToImplied(odds);
    if (!Number.isFinite(implied)) continue;
    const entry = groups.get(key) || {
      sport: p?.sport || "",
      market,
      player,
      side,
      line,
      game: p?.game || "",
      game_time: p?.game_time || "",
      books: [],
    };
    entry.books.push({ book: String(p?.book || ""), odds, implied });
    if (!entry.game && p?.game) entry.game = p.game;
    if (!entry.game_time && p?.game_time) entry.game_time = p.game_time;
    groups.set(key, entry);
  }
  const recs = [];
  for (const entry of groups.values()) {
    if (entry.books.length < Math.max(2, minBooks)) continue;
    const probs = entry.books.map((b) => b.implied);
    const medianProb = medianNumber(probs);
    if (!Number.isFinite(medianProb)) continue;
    const best = entry.books.reduce((acc, cur) => (cur.implied < acc.implied ? cur : acc), entry.books[0]);
    const edgePct = (medianProb - best.implied) * 100;
    if (!Number.isFinite(edgePct) || edgePct < minEdgePct) continue;
    const consensusOdds = impliedToAmerican(medianProb);
    recs.push({
      sport: entry.sport,
      market: entry.market,
      player: entry.player,
      side: entry.side,
      line: entry.line,
      odds: best.odds,
      book: best.book,
      game: entry.game,
      game_time: entry.game_time,
      edge: Math.round(edgePct * 10) / 10,
      consensus_odds: consensusOdds,
      books: entry.books.length,
    });
  }
  recs.sort((a, b) => Number(b.edge || 0) - Number(a.edge || 0));
  return recs.slice(0, maxRecs);
}

function sanitizeEventName(raw) {
  const name = String(raw || "").trim().toLowerCase();
  if (!name) return "";
  return name.replace(/[^a-z0-9._-]/g, "").slice(0, 64);
}

function hashUserId(value) {
  const str = String(value || "");
  let hash = 5381;
  for (let i = 0; i < str.length; i += 1) {
    hash = ((hash << 5) + hash) ^ str.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
}

function emailVerifyCacheRequest(requestUrl, key) {
  const u = new URL(requestUrl);
  u.pathname = `/__email_verify/${encodeURIComponent(key)}`;
  u.search = "";
  return new Request(u.toString(), { method: "GET" });
}

async function readEmailVerify(requestUrl, key) {
  const hit = await caches.default.match(emailVerifyCacheRequest(requestUrl, key));
  if (!hit) return null;
  const payload = await hit.json().catch(() => null);
  if (!payload) return null;
  if (payload.expires_ts && payload.expires_ts > 0 && Date.now() > payload.expires_ts) {
    await caches.default.delete(emailVerifyCacheRequest(requestUrl, key));
    return null;
  }
  return payload;
}

async function writeEmailVerify(requestUrl, key, payload) {
  const ttl = payload?.verified ? EMAIL_VERIFY_VERIFIED_TTL_SECONDS : EMAIL_VERIFY_TTL_SECONDS;
  const res = new Response(JSON.stringify(payload), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `max-age=${ttl}`,
    },
  });
  await caches.default.put(emailVerifyCacheRequest(requestUrl, key), res);
}

function telemetryCacheRequest(requestUrl, dateKey) {
  const u = new URL(requestUrl);
  u.pathname = `/__telemetry_daily/${TELEMETRY_CACHE_VERSION}/${dateKey}`;
  u.search = "";
  return new Request(u.toString(), { method: "GET" });
}

async function readTelemetryDay(requestUrl, dateKey) {
  const hit = await caches.default.match(telemetryCacheRequest(requestUrl, dateKey));
  if (!hit) return null;
  const payload = await hit.json().catch(() => null);
  if (!payload || payload.date !== dateKey) return null;
  return payload;
}

async function writeTelemetryDay(requestUrl, dateKey, payload) {
  const resp = new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "public, max-age=31536000",
      "x-algobets-origin": "cloudflare-telemetry",
    },
  });
  await caches.default.put(telemetryCacheRequest(requestUrl, dateKey), resp);
}

function emptyTelemetryDay(dateKey) {
  return {
    date: dateKey,
    total: 0,
    events: {},
    updated_at: Date.now(),
  };
}

function bumpMap(map, key, inc = 1) {
  if (!map || !key) return;
  map[key] = Number(map[key] || 0) + inc;
}

function applyTelemetryEvent(day, { event, plan, tier, billing, userHash }) {
  if (!event) return;
  const safePlan = normalizePlan(plan || PLAN_FREE);
  const row = day.events[event] || {
    count: 0,
    unique: 0,
    by_plan: {},
    by_tier: {},
    by_billing: {},
    users: [],
  };
  row.count += 1;
  bumpMap(row.by_plan, safePlan);
  if (tier) bumpMap(row.by_tier, normalizePlan(tier));
  if (billing) bumpMap(row.by_billing, String(billing || "").toLowerCase());
  if (userHash && FUNNEL_UNIQUE_EVENTS.has(event)) {
    if (!Array.isArray(row.users)) row.users = [];
    if (!row.users.includes(userHash)) {
      if (row.users.length < TELEMETRY_UNIQUE_CAP) row.users.push(userHash);
      row.unique = row.users.length;
    }
  }
  day.events[event] = row;
  day.total += 1;
  day.updated_at = Date.now();
}

function adminAuthorized(request, env) {
  const key = String(env?.ADMIN_API_KEY || env?.ADMIN_KEY || "").trim();
  if (!key) return false;
  const headerKey = String(request.headers.get("x-admin-key") || "").trim();
  const auth = String(request.headers.get("authorization") || "").trim();
  return headerKey === key || auth === `Bearer ${key}`;
}

function mergeTelemetryMaps(target, source) {
  if (!source) return;
  for (const [k, v] of Object.entries(source)) {
    target[k] = Number(target[k] || 0) + Number(v || 0);
  }
}

function mergeTelemetryEvent(target, source) {
  target.count = Number(target.count || 0) + Number(source.count || 0);
  target.unique = Number(target.unique || 0) + Number(source.unique || 0);
  mergeTelemetryMaps(target.by_plan, source.by_plan);
  mergeTelemetryMaps(target.by_tier, source.by_tier);
  mergeTelemetryMaps(target.by_billing, source.by_billing);
}

function buildFunnel(aggregate, steps) {
  let prev = null;
  return steps.map((step) => {
    const row = aggregate.events[step] || {};
    const count = Number(row.count || 0);
    const unique = Number(row.unique || 0);
    const conv = prev && prev > 0 ? (count / prev) * 100 : null;
    prev = count;
    return { event: step, count, unique, conversion_from_prev_pct: conv };
  });
}

function buildPropsCacheRequest(requestUrl, sport) {
  const u = new URL(requestUrl);
  u.pathname = `/__props_cache/${PROPS_CACHE_VERSION}`;
  u.search = "";
  u.searchParams.set("sport", String(sport || "all").trim().toLowerCase() || "all");
  return new Request(u.toString(), { method: "GET" });
}

function buildModelSummaryRequest(requestUrl, dateKey) {
  const u = new URL(requestUrl);
  u.pathname = `/__model_summary/${dateKey}`;
  u.search = "";
  return new Request(u.toString(), { method: "GET" });
}

async function readModelSummary(requestUrl, dateKey) {
  const hit = await caches.default.match(buildModelSummaryRequest(requestUrl, dateKey));
  if (!hit) return null;
  const payload = await hit.json().catch(() => null);
  return payload && typeof payload === "object" ? payload : null;
}

async function readPropsCache(requestUrl, sport, nowMs) {
  const hit = await caches.default.match(buildPropsCacheRequest(requestUrl, sport));
  if (!hit) return null;
  const createdAtMs = Number(hit.headers.get("x-algobets-cache-created-at-ms") || 0);
  const ttlSec = Number(hit.headers.get("x-algobets-cache-ttl-seconds") || 0);
  const payload = await hit.json().catch(() => null);
  if (!payload || !createdAtMs || !ttlSec) return null;
  const ageSeconds = Math.max(0, Math.floor((nowMs - createdAtMs) / 1000));
  return {
    payload,
    ageSeconds,
    ttlSec,
    fresh: ageSeconds <= ttlSec,
    createdAtMs,
  };
}

async function writePropsCache(requestUrl, sport, ttlSec, payload, nowMs) {
  const resp = new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${ttlSec}`,
      "x-algobets-cache-created-at-ms": String(nowMs),
      "x-algobets-cache-ttl-seconds": String(ttlSec),
      "x-algobets-origin": "cloudflare-props-cache",
    },
  });
  await caches.default.put(buildPropsCacheRequest(requestUrl, sport), resp);
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

async function fetchWithTimeout(url, init = {}, timeoutMs = 8000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...init, signal: ctrl.signal });
    clearTimeout(t);
    return res;
  } catch (err) {
    clearTimeout(t);
    throw err;
  }
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

function authTokenValid(token) {
  const t = String(token || "").trim();
  return t.length >= 16 && !/^undefined|null$/i.test(t);
}

function requireUserAuth(request) {
  const auth = String(request.headers.get("authorization") || "").trim();
  if (!auth.startsWith("Bearer ")) return false;
  const token = auth.replace(/^Bearer\s+/i, "").trim();
  return authTokenValid(token);
}

function defaultAgentHealth() {
  return {
    conditional: {
      best_line_ev: true,
      market_velocity: true,
      consensus_devig: true,
      sentiment: false,
      injury: false,
      travel_fatigue: false,
      public_fade: false,
      lineup_parser: false,
    },
  };
}

function resolveOddsApiKey(env) {
  const keys = ["ODDS_API_KEY", "THE_ODDS_API_KEY", "ODDSAPI_KEY", "ODDS_KEY", "ODDS_API_V4_KEY"];
  for (const key of keys) {
    const value = String(env?.[key] || "").trim();
    if (value) return value;
  }
  return "";
}

function normalizeSportKey(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return "";
  const plain = raw.replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
  return SCORE_SPORT_MAP[raw] || SCORE_SPORT_MAP[plain] || "";
}

function normalizeTeamLabel(value) {
  const s = String(value || "").trim().toLowerCase();
  if (!s) return "";
  return s.replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
}

function teamAliases(value) {
  const base = normalizeTeamLabel(value);
  if (!base) return new Set();
  const tokens = base.split(" ").filter(Boolean);
  const out = new Set([base]);
  if (tokens.length) out.add(tokens[tokens.length - 1]);
  if (tokens.length >= 2) out.add(tokens.slice(-2).join(" "));
  return out;
}

function splitGameTeams(game) {
  const raw = String(game || "");
  if (raw.includes("@")) {
    const [away, home] = raw.split("@", 2);
    return [away.trim(), home.trim()];
  }
  const vsParts = raw.split(/\bvs\.?\b/i);
  if (vsParts.length >= 2) return [String(vsParts[0] || "").trim(), String(vsParts[1] || "").trim()];
  return [raw.trim(), ""];
}

function gameKeyVariants(away, home) {
  const variants = new Set();
  for (const a of teamAliases(away)) {
    for (const h of teamAliases(home)) {
      variants.add(`${a} @ ${h}`);
      variants.add(`${a} vs ${h}`);
    }
  }
  return variants;
}

function scoreNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function parseTrackedMarket(pick) {
  const market = String(pick?.market || "").trim().toLowerCase();
  if (market) return market;
  const bet = String(pick?.bet || "").toLowerCase();
  if (bet.includes(" ml")) return "moneyline";
  if (bet.startsWith("over ") || bet.startsWith("under ")) return "total";
  const spreadMatch = bet.match(/([+-]\d+(\.\d+)?)\s*$/);
  if (spreadMatch) return "spread";
  return "moneyline";
}

function parseTrackedSelection(pick, scoreRow) {
  const bet = String(pick?.bet || "").trim();
  const market = parseTrackedMarket(pick);
  const away = String(scoreRow?.away_team || "").trim();
  const home = String(scoreRow?.home_team || "").trim();
  if (market === "total") {
    const totalMatch = bet.match(/\b(over|under)\s+(\d+(\.\d+)?)/i);
    if (!totalMatch) return { market };
    return { market, side: totalMatch[1].toLowerCase(), line: Number(totalMatch[2]) };
  }
  if (market === "spread") {
    const spreadMatch = bet.match(/^(.*?)\s+([+-]\d+(\.\d+)?)$/);
    if (!spreadMatch) return { market };
    const team = String(spreadMatch[1] || "").trim();
    const normTeam = normalizeTeamLabel(team);
    const side = teamAliases(home).has(normTeam) || teamAliases(home).has(normTeam.split(" ").slice(-1).join(" ")) ? "home" : "away";
    return { market, side, line: Number(spreadMatch[2]), team };
  }
  const mlTeam = bet.replace(/\s+ml$/i, "").trim();
  const normTeam = normalizeTeamLabel(mlTeam);
  const side = teamAliases(home).has(normTeam) || teamAliases(home).has(normTeam.split(" ").slice(-1).join(" ")) ? "home" : "away";
  return { market: "moneyline", side, team: mlTeam };
}

function settleTrackedPick(pick, scoreRow) {
  const scores = Array.isArray(scoreRow?.scores) ? scoreRow.scores : [];
  if (!scoreRow?.completed || scores.length < 2) return null;
  const scoreMap = new Map();
  for (const item of scores) {
    const team = normalizeTeamLabel(item?.name);
    const score = scoreNumber(item?.score);
    if (team && score != null) scoreMap.set(team, score);
  }
  const away = String(scoreRow?.away_team || "").trim();
  const home = String(scoreRow?.home_team || "").trim();
  const awayScore = scoreMap.get(normalizeTeamLabel(away));
  const homeScore = scoreMap.get(normalizeTeamLabel(home));
  if (awayScore == null || homeScore == null) return null;
  const selection = parseTrackedSelection(pick, scoreRow);
  let status = "pending";
  if (selection.market === "moneyline") {
    if (homeScore === awayScore) status = "push";
    else {
      const pickedHome = selection.side === "home";
      const homeWon = homeScore > awayScore;
      status = pickedHome === homeWon ? "win" : "loss";
    }
  } else if (selection.market === "spread") {
    const line = Number(selection.line || 0);
    const pickedScore = selection.side === "home" ? homeScore : awayScore;
    const oppScore = selection.side === "home" ? awayScore : homeScore;
    const adjusted = pickedScore + line;
    if (adjusted === oppScore) status = "push";
    else status = adjusted > oppScore ? "win" : "loss";
  } else if (selection.market === "total") {
    const total = homeScore + awayScore;
    const line = Number(selection.line || 0);
    if (total === line) status = "push";
    else if (selection.side === "over") status = total > line ? "win" : "loss";
    else status = total < line ? "win" : "loss";
  }
  return {
    status,
    settled_ts: Math.floor(Date.now() / 1000),
    result: {
      away_team: away,
      home_team: home,
      away_score: awayScore,
      home_score: homeScore,
      total: awayScore + homeScore,
      commence_time: scoreRow?.commence_time || "",
    },
  };
}

async function fetchOddsApiScores(env, sportKey, daysFrom = 3) {
  const apiKey = resolveOddsApiKey(env);
  if (!apiKey || !sportKey) return [];
  const url = new URL(`https://api.the-odds-api.com/v4/sports/${encodeURIComponent(sportKey)}/scores`);
  url.searchParams.set("apiKey", apiKey);
  url.searchParams.set("daysFrom", String(Math.max(1, Math.min(7, Number(daysFrom || 3)))));
  url.searchParams.set("dateFormat", "iso");
  const res = await fetch(url.toString(), { method: "GET" });
  if (!res.ok) return [];
  const payload = await res.json().catch(() => []);
  return Array.isArray(payload) ? payload : [];
}

async function settleTrackedPicksForRequest(env, picks) {
  const rows = Array.isArray(picks) ? picks : [];
  const openRows = rows.filter((row) => !["win", "loss", "push"].includes(String(row?.status || "").toLowerCase()));
  if (!openRows.length) return rows;
  const oldestGameTs = openRows.reduce((min, row) => {
    const t = Date.parse(String(row?.game_time || ""));
    return Number.isFinite(t) ? Math.min(min, t) : min;
  }, Date.now());
  const ageDays = Math.ceil(Math.max(0, Date.now() - oldestGameTs) / 86400000);
  const daysFrom = Math.max(3, Math.min(7, ageDays + 1));
  const sportKeys = [...new Set(openRows.map((row) => normalizeSportKey(row?.sport_key || row?.sk || row?.sport)).filter(Boolean))];
  const scoreMap = new Map();
  for (const sportKey of sportKeys) {
    const scores = await fetchOddsApiScores(env, sportKey, daysFrom);
    for (const row of scores) {
      for (const key of gameKeyVariants(row?.away_team, row?.home_team)) {
        scoreMap.set(key, row);
      }
    }
  }
  return rows.map((row) => {
    const existing = String(row?.status || "").toLowerCase();
    if (["win", "loss", "push"].includes(existing)) return row;
    const [away, home] = splitGameTeams(row?.game || "");
    let match = null;
    for (const key of gameKeyVariants(away, home)) {
      if (scoreMap.has(key)) {
        match = scoreMap.get(key);
        break;
      }
    }
    if (!match) return { ...row, status: "pending" };
    const settled = settleTrackedPick(row, match);
    if (!settled) return { ...row, status: "pending" };
    return { ...row, ...settled };
  });
}

function dateKeyUtc(d = new Date()) {
  return d.toISOString().slice(0, 10);
}

function communityDisplayName(identifier, userId) {
  const email = String(identifier || "").trim().toLowerCase();
  if (email && email.includes("@")) {
    const [name, domain] = email.split("@");
    const pre = name.length > 2 ? `${name[0]}***${name[name.length - 1]}` : `${name[0] || "*"}*`;
    return `${pre}@${domain}`;
  }
  return userId ? `User ${String(userId).slice(-4)}` : "Member";
}

const POTD_CACHE_VERSION = "v2";

function potdCacheKey(dateKey) {
  return `__potd_cache/${POTD_CACHE_VERSION}/${dateKey}`;
}

function secondsUntilNextUtcDay(now = new Date()) {
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1, 0, 0, 5));
  return Math.max(60, Math.floor((next.getTime() - now.getTime()) / 1000));
}

function stableIndexFromDate(dateKey, count) {
  let hash = 0;
  for (let i = 0; i < dateKey.length; i += 1) {
    hash = (hash * 31 + dateKey.charCodeAt(i)) >>> 0;
  }
  return count ? (hash % count) : 0;
}

async function readPotdCache(dateKey) {
  const cache = caches.default;
  const key = new Request(`https://potd.local/${potdCacheKey(dateKey)}`);
  const hit = await cache.match(key);
  if (!hit) return null;
  return hit.json().catch(() => null);
}

async function writePotdCache(dateKey, pick) {
  if (!pick || typeof pick !== "object" || !Object.keys(pick).length) return;
  const cache = caches.default;
  const ttl = secondsUntilNextUtcDay();
  const key = new Request(`https://potd.local/${potdCacheKey(dateKey)}`);
  const resp = new Response(JSON.stringify({ pick, date: dateKey }), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": `public, max-age=${ttl}`,
    },
  });
  await cache.put(key, resp);
}

async function selectPotdPick(scanPayload) {
  if (!scanPayload || typeof scanPayload !== "object") return null;
  const dateKey = dateKeyUtc();
  const cached = await readPotdCache(dateKey);
  if (cached?.pick && typeof cached.pick === "object" && Object.keys(cached.pick).length) return cached.pick;
  let pool = Array.isArray(scanPayload?.picks) ? scanPayload.picks : [];
  if (!pool.length && Array.isArray(scanPayload?.watchlist)) pool = scanPayload.watchlist;
  const ranked = pool
    .filter((p) => p && typeof p === "object" && Number(p?.edge ?? p?.ev ?? 0) > 0)
    .slice()
    .sort((a, b) => {
      const edgeDiff = Number(b?.edge ?? b?.ev ?? 0) - Number(a?.edge ?? a?.ev ?? 0);
      if (edgeDiff) return edgeDiff;
      return Number(b?.confidence_calibrated ?? b?.confidence ?? 0) - Number(a?.confidence_calibrated ?? a?.confidence ?? 0);
    });
  if (!ranked.length) return null;
  const pick = ranked[0];
  await writePotdCache(dateKey, pick);
  return pick;
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

async function verifyGoogleIdToken(env, idToken) {
  const clientId = String(env?.GOOGLE_CLIENT_ID || "").trim();
  if (!clientId) throw Object.assign(new Error("Google auth is not configured."), { status: 503 });
  const verifyUrl = `https://oauth2.googleapis.com/tokeninfo?id_token=${encodeURIComponent(String(idToken || "").trim())}`;
  const res = await fetch(verifyUrl, { method: "GET" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(String(data?.error_description || data?.error || `Google token verify failed (${res.status})`)), { status: 401 });
  const aud = String(data?.aud || "").trim();
  if (!aud || aud !== clientId) throw Object.assign(new Error("Google audience mismatch."), { status: 401 });
  const exp = Number(data?.exp || 0);
  if (!Number.isFinite(exp) || exp <= Math.floor(Date.now() / 1000)) {
    throw Object.assign(new Error("Google token expired."), { status: 401 });
  }
  const sub = String(data?.sub || "").trim();
  const email = String(data?.email || "").trim().toLowerCase();
  const verified = String(data?.email_verified || "").toLowerCase() === "true";
  if (!sub || !email || !verified) throw Object.assign(new Error("Google account email must be verified."), { status: 401 });
  return {
    sub,
    email,
    name: String(data?.name || "").trim(),
    avatar: String(data?.picture || "").trim(),
  };
}

function decodeOAuthState(raw) {
  try {
    const txt = String(raw || "").trim();
    if (!txt) return null;
    const decoded = atob(txt);
    const obj = JSON.parse(decoded);
    return obj && typeof obj === "object" ? obj : null;
  } catch (_) {
    return null;
  }
}

async function verifyFacebookAuthCode(env, code, redirectUri) {
  const appId = String(env?.FACEBOOK_APP_ID || "").trim();
  const appSecret = String(env?.FACEBOOK_APP_SECRET || "").trim();
  if (!appId || !appSecret) throw Object.assign(new Error("Facebook auth is not configured."), { status: 503 });
  const tokenUrl = `https://graph.facebook.com/v19.0/oauth/access_token?client_id=${encodeURIComponent(appId)}&redirect_uri=${encodeURIComponent(redirectUri)}&client_secret=${encodeURIComponent(appSecret)}&code=${encodeURIComponent(String(code || "").trim())}`;
  const tokenRes = await fetch(tokenUrl, { method: "GET" });
  const tokenData = await tokenRes.json().catch(() => ({}));
  if (!tokenRes.ok) {
    const msg = String(tokenData?.error?.message || `Facebook token exchange failed (${tokenRes.status})`);
    const code = tokenData?.error?.code ? ` code:${tokenData.error.code}` : "";
    const sub = tokenData?.error?.error_subcode ? ` subcode:${tokenData.error.error_subcode}` : "";
    throw Object.assign(new Error(`${msg}${code}${sub}`.trim()), { status: 401 });
  }
  const accessToken = String(tokenData?.access_token || "").trim();
  if (!accessToken) throw Object.assign(new Error("Facebook access token missing."), { status: 401 });
  const meUrl = `https://graph.facebook.com/me?fields=id,name,email,picture.type(large)&access_token=${encodeURIComponent(accessToken)}`;
  const meRes = await fetch(meUrl, { method: "GET" });
  const me = await meRes.json().catch(() => ({}));
  if (!meRes.ok) {
    const msg = String(me?.error?.message || `Facebook profile fetch failed (${meRes.status})`);
    const code = me?.error?.code ? ` code:${me.error.code}` : "";
    const sub = me?.error?.error_subcode ? ` subcode:${me.error.error_subcode}` : "";
    throw Object.assign(new Error(`${msg}${code}${sub}`.trim()), { status: 401 });
  }
  const id = String(me?.id || "").trim();
  let email = String(me?.email || "").trim().toLowerCase();
  if (!id) throw Object.assign(new Error("Facebook account id is required."), { status: 401 });
  if (!email) email = `fb_${id}@users.algobets.local`;
  return {
    id,
    email,
    name: String(me?.name || "").trim(),
    avatar: String(me?.picture?.data?.url || "").trim(),
  };
}

async function createUpstreamSession(env, request, userId, email, method = "google") {
  const url = new URL(`${UPSTREAM}/api/auth/session`);
  const headers = new Headers({ "content-type": "application/json" });
  const proxyApiKey = String(env?.BACKEND_API_KEY || "").trim();
  if (proxyApiKey) headers.set("x-api-key", proxyApiKey);
  const upstream = await fetchWithTimeout(
    url.toString(),
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        user_id: userId,
        method: String(method || "google"),
        identifier: email,
      }),
    },
    8000,
  );
  const data = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    throw Object.assign(new Error(String(data?.detail || `Session creation failed (${upstream.status})`)), { status: upstream.status });
  }
  return data;
}

function createLocalSession({ userId, method, identifier, name, avatar }) {
  const uid = normalizeUserId(userId) || `usr_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  return {
    token: `local_${crypto.randomUUID().replace(/-/g, "")}`,
    user_id: uid,
    profile: {
      method: String(method || "guest"),
      identifier: String(identifier || "").trim(),
      name: String(name || "").trim(),
      avatar: String(avatar || "").trim(),
    },
  };
}

function authReturnUrl(requestUrl, raw = "") {
  const source = new URL(requestUrl);
  const fallback = `${source.origin.replace(/\/+$/, "")}/app`;
  try {
    const candidate = String(raw || "").trim();
    if (!candidate) return fallback;
    const url = new URL(candidate, source.origin);
    if (url.origin !== source.origin) return fallback;
    return url.toString();
  } catch (_) {
    return fallback;
  }
}

async function resolveEffectivePlanForUser(env, userId, userRec, nowSec, clientPlanHint = PLAN_FREE, identifierHint = "") {
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
  const identifier = String(userRec?.identifier || identifierHint || "").trim().toLowerCase();
  if (isVipOverrideEmail(env, identifier) && planRank(plan) < planRank(PLAN_VIP)) plan = PLAN_VIP;
  if (isPremiumOverrideEmail(env, identifier) && planRank(plan) < planRank(PLAN_PREMIUM)) plan = PLAN_PREMIUM;
  return normalizePlan(plan);
}

export async function onRequest(context) {
  const { request, params, env } = context;
  const method = request.method.toUpperCase();
  const rawPath = (Array.isArray(params.path) ? params.path.join("/") : String(params.path || "")).replace(/,/g, "/");
  const cleanPath = rawPath.replace(/^\/+/, "");
  const nowSec = Math.floor(Date.now() / 1000);
  const userId = String(request.headers.get("x-user-id") || "").trim().toLowerCase();
  const authOk = requireUserAuth(request);
  const userEmailHint = String(request.headers.get("x-user-email") || "").trim().toLowerCase();
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

  if (method === "GET" && cleanPath === "potd") {
    try {
      const payload = await getScanPayload(env, { force: false });
      const pick = selectPotdPick(payload);
      return json(
        {
          date: new Date().toISOString().slice(0, 10),
          generated_at: new Date().toISOString(),
          pick,
        },
        200,
        "cloudflare-potd",
      );
    } catch (err) {
      return json(
        {
          date: new Date().toISOString().slice(0, 10),
          generated_at: new Date().toISOString(),
          pick: null,
          detail: String(err?.message || err || "POTD unavailable"),
        },
        503,
        "cloudflare-potd",
      );
    }
  }

  // Lightweight schedule proxy: reuse cached scan output to populate odds slates without extra frontend scans.
  if (method === "GET" && cleanPath === "games") {
    const url = new URL(request.url);
    const dayOffset = Number(url.searchParams.get("day_offset") || 0);
    const range = String(url.searchParams.get("range") || "").toLowerCase();
    const boardDay = dayOffset >= 1 ? "tomorrow" : "today";
    try {
      if (range === "week") {
        const today = new Date();
        const dates = [];
        for (let i = 0; i < 7; i += 1) {
          const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() + i));
          dates.push(d.toISOString().slice(0, 10));
        }
        const combined = [];
        for (const d of dates) {
          const dayGames = await fetchSportsDbDay(env, d);
          combined.push(...dayGames);
        }
        return json(
          {
            games: combined,
            total: combined.length,
            sports_covered: Array.from(new Set(combined.map((g) => g.sport).filter(Boolean))),
            board_day: "week",
            day_offset: dayOffset,
            source: "cloudflare-sportsdb",
          },
          200,
          "cloudflare-games-proxy",
        );
      }
      if (dayOffset >= 1) {
        const d = new Date();
        d.setUTCDate(d.getUTCDate() + dayOffset);
        const dateStr = d.toISOString().slice(0, 10);
        const games = await fetchSportsDbDay(env, dateStr);
        return json(
          {
            games,
            total: games.length,
            sports_covered: Array.from(new Set(games.map((g) => g.sport).filter(Boolean))),
            board_day: boardDay,
            day_offset: dayOffset,
            source: "cloudflare-sportsdb",
          },
          200,
          "cloudflare-games-proxy",
        );
      }
      const payload = await getScanPayload(env, { boardDay, force: false });
      const games = Array.isArray(payload?.games) ? payload.games : [];
      return json(
        {
          games,
          total: games.length,
          sports_covered: Array.from(new Set(games.map((g) => g.sport).filter(Boolean))),
          board_day: boardDay,
          day_offset: dayOffset,
          source: "cloudflare-odds-engine",
          quota_remaining: payload?.quota_remaining ?? null,
        },
        200,
        "cloudflare-games-proxy",
      );
    } catch (err) {
      return json({ games: [], total: 0, detail: "Schedule unavailable" }, 503, "cloudflare-games-proxy");
    }
  }

  if (method === "GET" && cleanPath === "props") {
    const nowMs = Date.now();
    const urlParams = new URL(request.url).searchParams;
    const sport = String(urlParams.get("sport") || "").trim().toLowerCase();
    const forceRefresh = String(urlParams.get("refresh") || "").toLowerCase() === "true";
    const planHint = normalizePlan(request.headers.get("x-user-plan") || PLAN_FREE);
    const effectivePlan = await resolveEffectivePlanForUser(env, userId, user, nowSec, planHint, userEmailHint);
    if (planRank(effectivePlan) < planRank(PLAN_VIP)) {
      return json({ detail: "Upgrade to VIP to access Props." }, 403, "cloudflare-props");
    }
    if (!PROPS_MARKETS_BY_SPORT[sport]) {
      return json({ detail: "Props currently supported for NBA and NFL." }, 400, "cloudflare-props");
    }
    const ttlSec = Math.max(300, Number(env?.PROPS_CACHE_TTL_SECONDS || 2 * 60 * 60));
    let cached = null;
    if (!forceRefresh) {
      cached = await readPropsCache(request.url, sport, nowMs);
      if (cached?.payload) {
        const payload = typeof structuredClone === "function"
          ? structuredClone(cached.payload)
          : JSON.parse(JSON.stringify(cached.payload));
        if (payload && typeof payload === "object" && !Array.isArray(payload)) {
          payload.scan_policy = {
            ...(payload.scan_policy || {}),
            served_from_cache: true,
            served_stale_cache: !cached.fresh,
            shared_cache_age_seconds: cached.ageSeconds,
            shared_cache_ttl_seconds: cached.ttlSec,
            budget_mode: "delayed_props",
          };
        }
        return json(payload, 200, "cloudflare-props-cache");
      }
    }
    const apiKey = resolveOddsApiKey(env);
    if (!apiKey) {
      return json({ detail: "Odds API key missing for props." }, 503, "cloudflare-props");
    }
    const markets = PROPS_MARKETS_BY_SPORT[sport] || [];
    const bookmakers = String(env?.PROPS_BOOKMAKERS || "draftkings,fanduel,betmgm").trim() || "draftkings,fanduel,betmgm";
    const maxEvents = Math.max(1, Math.min(2, Number(env?.PROPS_MAX_EVENTS_PER_SPORT || 1)));
    let remaining = null;
    let used = null;
    const props = [];
    try {
      const eventsRes = await fetch(`${ODDS_BASE}/sports/${sport}/events?apiKey=${encodeURIComponent(apiKey)}&dateFormat=iso`);
      const events = (await eventsRes.json().catch(() => [])) || [];
      const nowIso = new Date().toISOString();
      const upcoming = Array.isArray(events) ? events.filter(e => String(e?.commence_time || "") >= nowIso && e?.id) : [];
      upcoming.sort((a, b) => String(a?.commence_time || "").localeCompare(String(b?.commence_time || "")));
      const chosen = upcoming.slice(0, maxEvents);
      for (const ev of chosen) {
        const eventId = ev.id;
        if (!eventId) continue;
        const oddsUrl = `${ODDS_BASE}/sports/${sport}/events/${eventId}/odds?apiKey=${encodeURIComponent(apiKey)}&regions=us&markets=${encodeURIComponent(markets.join(","))}&bookmakers=${encodeURIComponent(bookmakers)}&oddsFormat=american&dateFormat=iso`;
        const r = await fetch(oddsUrl);
        remaining = r.headers.get("X-Requests-Remaining") || remaining;
        used = r.headers.get("X-Requests-Used") || used;
        if (!r.ok) continue;
        const payload = await r.json().catch(() => ({}));
        const books = Array.isArray(payload?.bookmakers) ? payload.bookmakers : [];
        for (const bm of books) {
          const book = String(bm?.title || "");
          for (const market of Array.isArray(bm?.markets) ? bm.markets : []) {
            const mkey = String(market?.key || "");
            for (const o of Array.isArray(market?.outcomes) ? market.outcomes : []) {
              const player = String(o?.description || o?.name || "").trim();
              const side = String(o?.name || "").trim();
              const line = o?.point;
              const odds = o?.price;
              if (line == null || odds == null) continue;
              props.push({
                sport,
                market: mkey,
                player,
                side,
                line,
                odds,
                book,
                game: `${ev?.away_team || ''} @ ${ev?.home_team || ''}`.trim(),
                game_time: ev?.commence_time || payload?.commence_time || "",
              });
            }
          }
        }
      }
      const minEdgePct = Number(env?.PROPS_MIN_EDGE_PCT || 1.5);
      const minBooks = Number(env?.PROPS_MIN_BOOKS || 3);
      const maxRecs = Number(env?.PROPS_MAX_RECS || 12);
      const initial = recommendProps(props, { minEdgePct, minBooks, maxRecs: Math.max(maxRecs * 2, 20) });
      let finalProps = initial;
      let modelMeta = {
        recommendation_model: "price_edge_vs_consensus",
        recommendation_min_edge_pct: minEdgePct,
        recommendation_min_books: minBooks,
      };
      if (sport === "basketball_nba") {
        const bdlKey = resolveBalldontlieKey(env);
        const minModelEdge = Number(env?.PROPS_MODEL_MIN_EDGE_PCT || 4);
        const modelGames = Number(env?.PROPS_MODEL_GAMES || 8);
        const modelMaxPlayers = Number(env?.PROPS_MODEL_MAX_PLAYERS || 12);
        const modelWeight = Number(env?.PROPS_MODEL_WEIGHT || 0.6);
        if (bdlKey) {
          const modeled = await applyNbaPropModel(initial, {
            apiKey: bdlKey,
            minModelEdgePct: minModelEdge,
            games: modelGames,
            maxPlayers: modelMaxPlayers,
            modelWeight,
          });
          if (modeled.props.length) {
            finalProps = modeled.props.slice(0, maxRecs);
          } else {
            finalProps = initial.slice(0, maxRecs);
          }
          modelMeta = {
            recommendation_model: "price_edge_vs_consensus + nba_recent_avg",
            recommendation_min_edge_pct: minEdgePct,
            model_source: modeled.props.length ? "balldontlie_recent_avg" : "balldontlie_unmatched",
            model_games: modelGames,
            model_min_edge_pct: minModelEdge,
            model_weight: modelWeight,
            model_fallback: modeled.props.length ? false : true,
            model_note: modeled.props.length ? "" : "Model edge filter not met; showing price-edge plays.",
            recommendation_min_books: minBooks,
          };
        } else {
          modelMeta = {
            recommendation_model: "price_edge_vs_consensus",
            recommendation_min_edge_pct: minEdgePct,
            model_source: "none",
            model_note: "NBA model key not configured.",
            recommendation_min_books: minBooks,
          };
        }
      }
      if (cached?.payload && Array.isArray(cached.payload?.props)) {
        const prev = cached.payload.props;
        const prevMap = new Map();
        for (const p of prev) {
          const key = `${normalizePlayerName(p?.player || "")}|${String(p?.market || "")}|${String(p?.side || "")}|${String(p?.line || "")}`;
          if (!key) continue;
          prevMap.set(key, p);
        }
        finalProps = finalProps.map((p) => {
          const key = `${normalizePlayerName(p?.player || "")}|${String(p?.market || "")}|${String(p?.side || "")}|${String(p?.line || "")}`;
          const prevRow = prevMap.get(key);
          if (!prevRow || !Number.isFinite(prevRow?.odds)) return { ...p, clv_trend: "new" };
          const prevOdds = Number(prevRow.odds);
          const curOdds = Number(p.odds);
          if (!Number.isFinite(curOdds)) return { ...p, clv_trend: "new" };
          if (curOdds > prevOdds) return { ...p, clv_trend: "improving" };
          if (curOdds < prevOdds) return { ...p, clv_trend: "worse" };
          return { ...p, clv_trend: "flat" };
        });
        const improving = finalProps.filter((p) => p.clv_trend === "improving");
        const rest = finalProps.filter((p) => p.clv_trend !== "improving");
        finalProps = improving.concat(rest).slice(0, maxRecs);
        modelMeta.clv_proxy = true;
        modelMeta.clv_proxy_note = "Ranked with odds-improving props first.";
      }
      const responsePayload = {
        sport,
        props: finalProps,
        count: finalProps.length,
        total_scanned: props.length,
        ...modelMeta,
        budget: {
          monthly_credit_cap: Number(env?.PROPS_MONTHLY_CREDIT_CAP || 0),
          quota_used_last: used ? Number(used) : 0,
          quota_remaining: remaining ? Number(remaining) : 0,
          within_budget: true,
        },
      };
      await writePropsCache(request.url, sport, ttlSec, responsePayload, nowMs);
      return json(responsePayload, 200, "cloudflare-props");
    } catch (err) {
      return json({ detail: String(err?.message || "Props unavailable."), props: [] }, 503, "cloudflare-props");
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
    const emailVerifyDisabled = String(env?.EMAIL_VERIFY_DISABLED || "").trim().toLowerCase() === "true";
    return json(
      {
        vip_discord_url: String(env?.VIP_DISCORD_URL || ""),
        google_client_id: String(env?.GOOGLE_CLIENT_ID || ""),
        facebook_app_id: String(env?.FACEBOOK_APP_ID || ""),
        billing_enabled: billing.enabled,
        auth_required: true,
        discord_role_sync_enabled: false,
        low_data_mode_global: true,
        email_verify_disabled: emailVerifyDisabled,
      },
      200,
      "cloudflare-config",
    );
  }

  if (method === "GET" && cleanPath === "agents/health") {
    return json({ health: defaultAgentHealth() }, 200, "cloudflare-agents-health");
  }

  if (method === "POST" && (cleanPath === "marketing/consent" || cleanPath === "marketing/subscribe")) {
    const body = await request.json().catch(() => ({}));
    return json(
      {
        ok: true,
        recorded: true,
        marketing_opt_in: !!body?.marketing_opt_in,
      },
      200,
      "cloudflare-marketing",
    );
  }

  if (method === "GET" && cleanPath === "auth/email/verify/status") {
    const url = new URL(request.url);
    const email = normalizeEmail(url.searchParams.get("email") || "");
    if (!email) return json({ detail: "email required." }, 422, "cloudflare-auth");
    const key = emailVerifyKey(email);
    const rec = await getEmailVerifyRecord(request.url, key);
    return json(
      {
        verified: !!rec?.verified,
        pending: !!rec?.pending,
        expires_ts: Number(rec?.expires_ts || 0),
      },
      200,
      "cloudflare-auth",
    );
  }

  if (method === "POST" && cleanPath === "auth/email/verify/request") {
    const body = await request.json().catch(() => ({}));
    const uid = normalizeUserId(body?.user_id || userId || "");
    const email = normalizeEmail(body?.email || "");
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return json({ detail: "Valid email required." }, 422, "cloudflare-auth");
    }
    const code = generateEmailCode();
    const expires_ts = Date.now() + EMAIL_VERIFY_TTL_SECONDS * 1000;
    await setEmailVerifyRecord(request.url, emailVerifyKey(email), {
      code,
      pending: true,
      verified: false,
      expires_ts,
    });
    try {
      await sendVerificationEmail(env, email, code);
      return json({ ok: true, pending: true, expires_ts }, 200, "cloudflare-auth");
    } catch (err) {
      return json({ detail: String(err?.message || "Email verification not configured.") }, 500, "cloudflare-auth");
    }
  }

  if (method === "POST" && cleanPath === "auth/email/verify/confirm") {
    const body = await request.json().catch(() => ({}));
    const uid = normalizeUserId(body?.user_id || userId || "");
    const email = normalizeEmail(body?.email || "");
    const code = String(body?.code || "").trim();
    if (!email || !code) return json({ detail: "email and code required." }, 422, "cloudflare-auth");
    const key = emailVerifyKey(email);
    const rec = await getEmailVerifyRecord(request.url, key);
    if (!rec || !rec.code || rec.code !== code) return json({ detail: "Invalid or expired code." }, 401, "cloudflare-auth");
    await setEmailVerifyRecord(request.url, key, { ...rec, verified: true, pending: false, expires_ts: 0 });
    if (uid) {
      const existing = userState.get(uid) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };
      userState.set(uid, { ...existing, identifier: email, email_verified: true });
    }
    return json({ verified: true }, 200, "cloudflare-auth");
  }

  if (method === "POST" && cleanPath === "auth/session") {
    const body = await request.clone().json().catch(() => ({}));
    // Always passthrough to upstream auth/session so bearer token validates on upstream protected routes.
    const url = toUpstreamUrl(request.url, rawPath);
    const init = {
      method,
      headers: forwardHeaders(request),
      redirect: "follow",
      body: request.body,
    };
    const hasApiKey = !!String(request.headers.get("x-api-key") || "").trim();
    const proxyApiKey = String(env?.BACKEND_API_KEY || "").trim();
    if (!hasApiKey && proxyApiKey) init.headers.set("x-api-key", proxyApiKey);
    try {
      const upstream = await fetchWithTimeout(url, init, 8000);
      const data = await upstream.clone().json().catch(() => ({}));
      const sessionUserId = normalizeUserId(data?.user_id || body?.user_id || userId);
      const identifier = String(data?.profile?.identifier || body?.identifier || "").trim().toLowerCase();
      if (sessionUserId) {
        const existing = userState.get(sessionUserId) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };
        let nextPlan = existing.plan;
        if (isVipOverrideEmail(env, identifier) && planRank(nextPlan) < planRank(PLAN_VIP)) nextPlan = PLAN_VIP;
        else if (isPremiumOverrideEmail(env, identifier) && planRank(nextPlan) < planRank(PLAN_PREMIUM)) nextPlan = PLAN_PREMIUM;
        userState.set(sessionUserId, {
          ...existing,
          identifier,
          plan: nextPlan,
        });
      }
      if (upstream.ok) {
        const outHeaders = new Headers(upstream.headers);
        outHeaders.set("x-algobets-proxy", "cloudflare-pages");
        return new Response(upstream.body, {
          status: upstream.status,
          statusText: upstream.statusText,
          headers: outHeaders,
        });
      }
      if (upstream.status < 500) {
        const outHeaders = new Headers(upstream.headers);
        outHeaders.set("x-algobets-proxy", "cloudflare-pages");
        return new Response(upstream.body, {
          status: upstream.status,
          statusText: upstream.statusText,
          headers: outHeaders,
        });
      }
    } catch (_) {}
    const fallback = createLocalSession({
      userId: body?.user_id || userId,
      method: body?.method || "guest",
      identifier: body?.identifier || "",
    });
    const fallbackId = normalizeUserId(fallback.user_id);
    if (fallbackId) {
      const existing = userState.get(fallbackId) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };
      const identifier = String(fallback?.profile?.identifier || "").trim().toLowerCase();
      let nextPlan = existing.plan;
      if (isVipOverrideEmail(env, identifier) && planRank(nextPlan) < planRank(PLAN_VIP)) nextPlan = PLAN_VIP;
      else if (isPremiumOverrideEmail(env, identifier) && planRank(nextPlan) < planRank(PLAN_PREMIUM)) nextPlan = PLAN_PREMIUM;
      userState.set(fallbackId, { ...existing, identifier, plan: nextPlan });
    }
    return json(fallback, 200, "cloudflare-auth");
  }

  if (method === "POST" && cleanPath === "auth/google") {
    try {
      const body = await request.json().catch(() => ({}));
      const idToken = String(body?.id_token || "").trim();
      if (!idToken) return json({ detail: "id_token is required." }, 422, "cloudflare-auth");
      const claims = await verifyGoogleIdToken(env, idToken);
      let session;
      try {
        session = await createUpstreamSession(env, request, `g_${claims.sub}`, claims.email, "google");
      } catch (_) {
        session = createLocalSession({
          userId: `g_${claims.sub}`,
          method: "google",
          identifier: claims.email,
          name: claims.name,
          avatar: claims.avatar,
        });
      }
      const sessionUserId = normalizeUserId(session?.user_id || `g_${claims.sub}`);
      if (sessionUserId) {
        const existing = userState.get(sessionUserId) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };
        let nextPlan = existing.plan;
        if (isVipOverrideEmail(env, claims.email) && planRank(nextPlan) < planRank(PLAN_VIP)) nextPlan = PLAN_VIP;
        else if (isPremiumOverrideEmail(env, claims.email) && planRank(nextPlan) < planRank(PLAN_PREMIUM)) nextPlan = PLAN_PREMIUM;
        userState.set(sessionUserId, {
          ...existing,
          identifier: claims.email,
          plan: nextPlan,
        });
      }
      return json(
        {
          token: String(session?.token || "").trim(),
          user_id: String(session?.user_id || `g_${claims.sub}`).trim().toLowerCase(),
          profile: {
            identifier: claims.email,
            name: claims.name,
            avatar: claims.avatar,
          },
        },
        200,
        "cloudflare-auth",
      );
    } catch (err) {
      return json({ detail: String(err?.message || "Google sign-in failed.") }, Number(err?.status || 401), "cloudflare-auth");
    }
  }

  if (method === "POST" && cleanPath === "auth/google/redirect") {
    try {
      const form = await request.formData().catch(() => null);
      const idToken = String(form?.get("credential") || "").trim();
      const returnTo = authReturnUrl(request.url, form?.get("return_to") || "");
      if (!idToken) return Response.redirect(returnTo, 302);
      const claims = await verifyGoogleIdToken(env, idToken);
      let session;
      try {
        session = await createUpstreamSession(env, request, `g_${claims.sub}`, claims.email, "google");
      } catch (_) {
        session = createLocalSession({
          userId: `g_${claims.sub}`,
          method: "google",
          identifier: claims.email,
          name: claims.name,
          avatar: claims.avatar,
        });
      }
      const sessionUserId = normalizeUserId(session?.user_id || `g_${claims.sub}`);
      if (sessionUserId) {
        const existing = userState.get(sessionUserId) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };
        let nextPlan = existing.plan;
        if (isVipOverrideEmail(env, claims.email) && planRank(nextPlan) < planRank(PLAN_VIP)) nextPlan = PLAN_VIP;
        else if (isPremiumOverrideEmail(env, claims.email) && planRank(nextPlan) < planRank(PLAN_PREMIUM)) nextPlan = PLAN_PREMIUM;
        userState.set(sessionUserId, {
          ...existing,
          identifier: claims.email,
          plan: nextPlan,
        });
      }
      const next = new URL(returnTo);
      next.searchParams.set("uid", String(session?.user_id || `g_${claims.sub}`).trim().toLowerCase());
      next.searchParams.set("eauth", String(session?.token || "").trim());
      next.searchParams.set("euser", String(claims?.email || "").trim().toLowerCase());
      return Response.redirect(next.toString(), 302);
    } catch (_) {
      const next = authReturnUrl(request.url, "");
      return Response.redirect(next, 302);
    }
  }

  if (method === "GET" && cleanPath === "auth/facebook/redirect") {
    try {
      const url = new URL(request.url);
      const code = String(url.searchParams.get("code") || "").trim();
      const stateRaw = String(url.searchParams.get("state") || "").trim();
      const state = decodeOAuthState(stateRaw);
      const returnTo = authReturnUrl(request.url, state?.rt || "");
      const redirectUri = `${url.origin}/api/auth/facebook/redirect`;
      if (!code) {
        const noCode = new URL(returnTo);
        noCode.searchParams.set("fb_error", "no_code");
        return Response.redirect(noCode.toString(), 302);
      }
      const claims = await verifyFacebookAuthCode(env, code, redirectUri);
      let session;
      try {
        session = await createUpstreamSession(env, request, `fb_${claims.id}`, claims.email, "facebook");
      } catch (_) {
        session = createLocalSession({
          userId: `fb_${claims.id}`,
          method: "facebook",
          identifier: claims.email,
          name: claims.name,
          avatar: claims.avatar,
        });
      }
      const sessionUserId = normalizeUserId(session?.user_id || `fb_${claims.id}`);
      if (sessionUserId) {
        const existing = userState.get(sessionUserId) || { plan: PLAN_FREE, trialUntil: 0, trialClaimed: false };
        let nextPlan = existing.plan;
        if (isVipOverrideEmail(env, claims.email) && planRank(nextPlan) < planRank(PLAN_VIP)) nextPlan = PLAN_VIP;
        else if (isPremiumOverrideEmail(env, claims.email) && planRank(nextPlan) < planRank(PLAN_PREMIUM)) nextPlan = PLAN_PREMIUM;
        userState.set(sessionUserId, {
          ...existing,
          identifier: claims.email,
          plan: nextPlan,
        });
      }
      const next = new URL(returnTo);
      next.searchParams.set("uid", String(session?.user_id || `fb_${claims.id}`).trim().toLowerCase());
      next.searchParams.set("eauth", String(session?.token || "").trim());
      next.searchParams.set("euser", String(claims?.email || "").trim().toLowerCase());
      return Response.redirect(next.toString(), 302);
    } catch (err) {
      const next = new URL(authReturnUrl(request.url, ""));
      next.searchParams.set("fb_error", "auth_failed");
      if (err?.message) next.searchParams.set("fb_error_msg", String(err.message).slice(0, 180));
      return Response.redirect(next.toString(), 302);
    }
  }

  if (method === "POST" && cleanPath === "telemetry/event") {
    const body = await request.json().catch(() => ({}));
    const event = sanitizeEventName(body?.name || "");
    if (!event) return json({ detail: "Invalid event name." }, 400, "cloudflare-telemetry");
    const props = body?.props || {};
    const planHint = request.headers.get("x-user-plan") || PLAN_FREE;
    const tier = String(props?.tier || "").trim();
    const billingCycle = String(props?.billing_cycle || props?.billing || "").trim();
    const userHash = userId ? hashUserId(userId) : "";
    const dateKey = new Date().toISOString().slice(0, 10);
    const day = (await readTelemetryDay(request.url, dateKey)) || emptyTelemetryDay(dateKey);
    applyTelemetryEvent(day, { event, plan: planHint, tier, billing: billingCycle, userHash });
    await writeTelemetryDay(request.url, dateKey, day);
    return json({ ok: true }, 200, "cloudflare-telemetry");
  }

  if (method === "GET" && cleanPath === "admin/telemetry/funnel") {
    if (!adminAuthorized(request, env)) return json({ detail: "Unauthorized" }, 401, "cloudflare-telemetry");
    const url = new URL(request.url);
    const days = clampNumber(url.searchParams.get("days") || 30, 1, TELEMETRY_MAX_DAYS);
    const aggregate = { events: {}, days, from: "", to: "", updated_at: null };
    for (let i = 0; i < days; i += 1) {
      const d = new Date();
      d.setUTCDate(d.getUTCDate() - i);
      const dateKey = d.toISOString().slice(0, 10);
      if (i === 0) aggregate.to = dateKey;
      if (i === days - 1) aggregate.from = dateKey;
      const day = await readTelemetryDay(request.url, dateKey);
      if (!day || !day.events) continue;
      if (day.updated_at && (!aggregate.updated_at || day.updated_at > aggregate.updated_at)) {
        aggregate.updated_at = day.updated_at;
      }
      for (const [name, row] of Object.entries(day.events)) {
        const target = aggregate.events[name] || { count: 0, unique: 0, by_plan: {}, by_tier: {}, by_billing: {} };
        mergeTelemetryEvent(target, row || {});
        aggregate.events[name] = target;
      }
    }
    const funnel = {
      auth: buildFunnel(aggregate, ["auth_modal_opened", "auth_continue_clicked", "auth_completed"]),
      upgrade: buildFunnel(aggregate, ["upgrade_picker_opened", "upgrade_plan_selected", "checkout_started", "checkout_success"]),
    };
    return json({ ...aggregate, funnel }, 200, "cloudflare-telemetry");
  }

  const clientPlanHint = PLAN_FREE;

  if (method === "GET" && cleanPath === "plan") {
    const plan = await resolveEffectivePlanForUser(env, userId, user, nowSec, clientPlanHint, userEmailHint);
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

  if (method === "GET" && cleanPath === "tracker") {
    if (!authOk) return json({ detail: "Authorization required." }, 401, "cloudflare-tracker");
    if (!userId) return json({ detail: "x-user-id is required." }, 400, "cloudflare-tracker");
    if (!env?.ALGOBETS_TRACKED) {
      return json({ detail: "Tracked picks storage is not configured." }, 503, "cloudflare-tracker");
    }
    const picks = await readTrackedFromKv(env, userId);
    return json({ picks: picks || [] }, 200, "cloudflare-tracker");
  }

  if (method === "GET" && cleanPath === "model/summary") {
    if (!authOk) return json({ detail: "Authorization required." }, 401, "cloudflare-model");
    const url = new URL(request.url);
    const days = clampNumber(url.searchParams.get("days") || 7, 1, 30);
    const picks = await readTrackedFromKv(env, userId);
    const tracked = summarizeTrackedPicks(picks || [], days);
    const scanDays = [];
    for (let i = 0; i < days; i += 1) {
      const d = new Date();
      d.setUTCDate(d.getUTCDate() - i);
      const dateKey = d.toISOString().slice(0, 10);
      const summary = await readModelSummary(request.url, dateKey);
      if (summary) scanDays.push(summary);
    }
    return json({ tracked, scan_days: scanDays }, 200, "cloudflare-model");
  }

  if (method === "GET" && cleanPath === "model/walkforward") {
    if (!authOk) return json({ detail: "Authorization required." }, 401, "cloudflare-model");
    const url = new URL(request.url);
    const weeks = clampNumber(url.searchParams.get("weeks") || 12, 2, 52);
    const picks = await readTrackedFromKv(env, userId);
    const metrics = walkForwardMetricsFromRows(picks || [], weeks);
    return json({ scope: "user", user_id: userId, weeks_requested: weeks, total_rows: (picks || []).length, metrics }, 200, "cloudflare-model");
  }

  if (method === "GET" && cleanPath === "community/posts") {
    const url = new URL(request.url);
    const limit = clampNumber(url.searchParams.get("limit") || 50, 1, 100);
    const posts = await readCommunityPosts(env);
    return json({ posts: posts.slice(0, limit) }, 200, "cloudflare-community");
  }

  if (method === "POST" && cleanPath === "community/posts") {
    if (!authOk) return json({ detail: "Authorization required." }, 401, "cloudflare-community");
    if (!env?.ALGOBETS_COMMUNITY) return json({ detail: "Community storage not configured." }, 503, "cloudflare-community");
    const body = await request.json().catch(() => ({}));
    const bet = String(body?.bet || "").trim();
    const game = String(body?.game || "").trim();
    const odds = String(body?.odds || "").trim();
    if (!bet || !game) return json({ detail: "bet and game required." }, 422, "cloudflare-community");
    const mode = String(body?.mode || "pick").trim().toLowerCase() === "win" ? "win" : "pick";
    const text = String(body?.text || (mode === "win" ? "Cashed this edge." : "Sharing this edge.")).trim().slice(0, 140);
    const ev = Number(body?.ev || 0);
    const sport = String(body?.sport || "").trim();
    const now = Date.now();
    const dateKey = dateKeyUtc(new Date(now));
    const rateKey = communityRateKey(userId, dateKey);
    const rawCount = await env.ALGOBETS_COMMUNITY.get(rateKey);
    const nextCount = Number(rawCount || 0) + 1;
    const dailyCap = Math.max(1, Number(env?.COMMUNITY_DAILY_CAP || 5));
    if (nextCount > dailyCap) {
      return json({ detail: "Daily community post limit reached." }, 429, "cloudflare-community");
    }
    await env.ALGOBETS_COMMUNITY.put(rateKey, String(nextCount), { expirationTtl: 24 * 60 * 60 });
    const posts = await readCommunityPosts(env);
    const post = {
      id: `c_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`,
      user: communityDisplayName(userEmailHint, userId),
      user_id: userId,
      ts: now,
      mode,
      text,
      bet,
      game,
      odds,
      ev: Number.isFinite(ev) ? ev : null,
      sport,
      verified: true,
    };
    const nextPosts = [post, ...posts].slice(0, 500);
    await writeCommunityPosts(env, nextPosts);
    return json({ ok: true, post }, 200, "cloudflare-community");
  }

  if (method === "GET" && cleanPath === "community/leaderboard") {
    const url = new URL(request.url);
    const limit = clampNumber(url.searchParams.get("limit") || 20, 1, 50);
    const posts = await readCommunityPosts(env);
    const cutoff = Date.now() - (7 * 24 * 60 * 60 * 1000);
    const byUser = new Map();
    posts.filter(p => Number(p?.ts || 0) >= cutoff).forEach((p) => {
      const key = String(p?.user_id || p?.user || "").trim() || "anon";
      const row = byUser.get(key) || { user: p?.user || "Member", wins: 0, picks: 0, score: 0 };
      if (String(p?.mode || "") === "win") row.wins += 1;
      else row.picks += 1;
      row.score = (row.wins * 3) + row.picks;
      row.user = p?.user || row.user;
      byUser.set(key, row);
    });
    const leaders = Array.from(byUser.values())
      .sort((a, b) => Number(b.score || 0) - Number(a.score || 0))
      .slice(0, limit);
    return json({ leaders }, 200, "cloudflare-community");
  }

  if (method === "POST" && cleanPath === "community/delete") {
    if (!adminAuthorized(request, env)) return json({ detail: "Unauthorized" }, 401, "cloudflare-community");
    const body = await request.json().catch(() => ({}));
    const postId = String(body?.id || "").trim();
    if (!postId) return json({ detail: "id required." }, 422, "cloudflare-community");
    const posts = await readCommunityPosts(env);
    const next = posts.filter((p) => String(p?.id || "") !== postId);
    await writeCommunityPosts(env, next);
    return json({ ok: true }, 200, "cloudflare-community");
  }

  if (method === "POST" && cleanPath === "tracker") {
    if (!authOk) return json({ detail: "Authorization required." }, 401, "cloudflare-tracker");
    if (!userId) return json({ detail: "x-user-id is required." }, 400, "cloudflare-tracker");
    if (!env?.ALGOBETS_TRACKED) {
      return json({ detail: "Tracked picks storage is not configured." }, 503, "cloudflare-tracker");
    }
    const body = await request.json().catch(() => ({}));
    const mode = String(body?.mode || "replace").trim().toLowerCase();
    const incoming = normalizeTrackedPicksInput(body?.picks || []);
    let next = incoming;
    if (mode === "merge") {
      const existing = await readTrackedFromKv(env, userId);
      next = mergeTrackedPicks(existing || [], incoming);
    }
    await writeTrackedToKv(env, userId, next);
    return json({ ok: true, picks: next }, 200, "cloudflare-tracker");
  }

  if (method === "POST" && cleanPath === "tracker/settle") {
    if (!authOk) return json({ detail: "Authorization required." }, 401, "cloudflare-tracker");
    const body = await request.json().catch(() => ({}));
    const picks = Array.isArray(body?.picks) ? body.picks : [];
    const settled = await settleTrackedPicksForRequest(env, picks);
    const open = settled.filter((row) => !["win", "loss", "push"].includes(String(row?.status || "").toLowerCase())).length;
    const graded = settled.length - open;
    return json({ picks: settled, graded, open }, 200, "cloudflare-tracker");
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
  const resolvedUserPlan = await resolveEffectivePlanForUser(env, userId, user, nowSec, clientPlanHint, userEmailHint);
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
