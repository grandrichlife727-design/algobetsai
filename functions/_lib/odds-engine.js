const SPORT_META = {
  basketball_nba: { label: "NBA", emoji: "🏀" },
  americanfootball_nfl: { label: "NFL", emoji: "🏈" },
  icehockey_nhl: { label: "NHL", emoji: "🏒" },
  basketball_ncaab: { label: "NCAAB", emoji: "🎓" },
  baseball_mlb: { label: "MLB", emoji: "⚾" },
  soccer_epl: { label: "EPL", emoji: "⚽" },
  soccer_spain_la_liga: { label: "La Liga", emoji: "⚽" },
};

const DEFAULT_SPORTS = Object.keys(SPORT_META);
const DEFAULT_BOOKMAKERS = "draftkings,fanduel,pinnacle";
const LOW_QUOTA_BOOKMAKERS = "draftkings,fanduel";
const TTL_MS = 15 * 60 * 1000;
const MAX_UPCOMING_DAYS = 7;
const STARTED_GRACE_MINUTES = 2;
const SPORT_IDLE_BACKOFF_MS = 6 * 60 * 60 * 1000;
const EMPTY_SCAN_BACKOFF_MS = 20 * 60 * 1000;
const QUOTA_DEGRADE_THRESHOLD = 75;
const QUOTA_CACHE_ONLY_THRESHOLD = 25;
const MAX_DISPLAY_EDGE = 12;
const MIN_BOOKS_FOR_SIDE_MARKET = 2;
const MAX_ODDS_FOR_SIDE_MARKET = 200;
const cacheStore = new Map();
const sportBackoffUntil = new Map();
const ODDS_KEY_ENV_CANDIDATES = [
  "ODDS_API_KEY",
  "THE_ODDS_API_KEY",
  "ODDSAPI_KEY",
  "ODDS_KEY",
  "ODDS_API_V4_KEY",
];
let lastQuotaRemaining = null;
let lastEmptyScanAt = 0;

function resolveOddsApiKey(env) {
  for (const k of ODDS_KEY_ENV_CANDIDATES) {
    const v = String(env?.[k] || "").trim();
    if (v) return v;
  }
  return "";
}

function minutesUntil(isoTime) {
  const t = Date.parse(String(isoTime || ""));
  if (!Number.isFinite(t)) return null;
  return (t - Date.now()) / 60000;
}

function zonedDateKey(input, timeZone = "America/New_York") {
  const date = input instanceof Date ? input : new Date(input);
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const lookup = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return `${lookup.year || "0000"}-${lookup.month || "00"}-${lookup.day || "00"}`;
}

function withinUpcomingWindow(isoTime, maxDays = MAX_UPCOMING_DAYS) {
  const mins = minutesUntil(isoTime);
  if (mins == null) return false;
  const maxAhead = Math.max(1, Number(maxDays || MAX_UPCOMING_DAYS)) * 24 * 60;
  return mins >= -STARTED_GRACE_MINUTES && mins <= maxAhead;
}

function normalizeBoardDay() {
  return "today";
}

function withinBoardWindow(isoTime, boardDay = "today") {
  const mins = minutesUntil(isoTime);
  if (mins == null) return false;
  const eventKey = zonedDateKey(isoTime);
  const now = new Date();
  const todayKey = zonedDateKey(now);
  const tomorrowKey = zonedDateKey(new Date(now.getTime() + 24 * 60 * 60 * 1000));
  if (normalizeBoardDay(boardDay) === "tomorrow") return eventKey === tomorrowKey;
  return eventKey === todayKey && mins >= -STARTED_GRACE_MINUTES;
}

function clonePayload(payload) {
  return typeof structuredClone === "function"
    ? structuredClone(payload)
    : JSON.parse(JSON.stringify(payload));
}

function normalizeBookmakersCsv(raw, maxBooks = 3) {
  return String(raw || "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
    .slice(0, Math.max(1, Number(maxBooks || 3)))
    .join(",");
}

function bookmakersForBudget(env, quotaRemaining) {
  const lowQuota = Number.isFinite(quotaRemaining) && quotaRemaining > 0 && quotaRemaining <= QUOTA_DEGRADE_THRESHOLD;
  const fallback = lowQuota ? LOW_QUOTA_BOOKMAKERS : DEFAULT_BOOKMAKERS;
  return normalizeBookmakersCsv(String(env?.ODDS_BOOKMAKERS || fallback).trim() || fallback, lowQuota ? 2 : 3);
}

function impliedProbability(americanOdds) {
  const o = Number(americanOdds);
  if (!Number.isFinite(o) || o === 0) return null;
  if (o > 0) return 100 / (o + 100);
  return Math.abs(o) / (Math.abs(o) + 100);
}

function americanToDecimal(americanOdds) {
  const o = Number(americanOdds);
  if (!Number.isFinite(o) || o === 0) return null;
  if (o > 0) return o / 100 + 1;
  return 1 + 100 / Math.abs(o);
}

function expectedValuePct(americanOdds, fairProbability) {
  const dec = americanToDecimal(americanOdds);
  if (!dec || !Number.isFinite(fairProbability)) return 0;
  return ((fairProbability * dec) - 1) * 100;
}

function compressDisplayEdge(rawEv) {
  const ev = Number(rawEv);
  if (!Number.isFinite(ev)) return 0;
  if (ev <= 0) return ev;
  if (ev <= 8) return ev;
  const compressed = 8 + (1 - Math.exp(-(ev - 8) / 10)) * (MAX_DISPLAY_EDGE - 8);
  return Math.min(MAX_DISPLAY_EDGE, compressed);
}

function calibratedConfidence(rawEv, displayEv) {
  const raw = Number(rawEv);
  const shown = Number(displayEv);
  if (!Number.isFinite(raw) || !Number.isFinite(shown)) return 50;
  const score = Math.max(shown, Math.min(raw, 18));
  return Math.max(51, Math.min(89, Math.round(54 + score * 1.35)));
}

function normalizeBookKey(raw) {
  return String(raw || "").toLowerCase().replace(/[^a-z0-9_]/g, "");
}

function pickBetterLine(currOdds, nextOdds) {
  const curr = americanToDecimal(currOdds);
  const next = americanToDecimal(nextOdds);
  if (!curr) return true;
  if (!next) return false;
  return next > curr;
}

function median(values) {
  const arr = (Array.isArray(values) ? values : [])
    .map(Number)
    .filter((v) => Number.isFinite(v))
    .sort((a, b) => a - b);
  if (!arr.length) return null;
  const mid = Math.floor(arr.length / 2);
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

function formatLinePoint(point) {
  const n = Number(point);
  if (!Number.isFinite(n)) return "";
  return n.toFixed(2).replace(/\.?0+$/, "");
}

function signedLine(point) {
  const n = Number(point);
  if (!Number.isFinite(n)) return "";
  const out = formatLinePoint(Math.abs(n));
  if (!out) return "";
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${out}`;
}

function buildGameAndPicksFromEvent(sportKey, event, options = {}) {
  const meta = SPORT_META[sportKey] || { label: sportKey, emoji: "🎯" };
  const isSoccer = String(sportKey || "").toLowerCase().startsWith("soccer_");
  const boardDay = normalizeBoardDay(options?.boardDay || "today");
  const homeTeam = String(event?.home_team || "").trim();
  const awayTeam = String(event?.away_team || "").trim();
  const commenceTime = String(event?.commence_time || "");
  const gameLabel = `${awayTeam} @ ${homeTeam}`;

  const moneylineByBook = {};
  const homeOddsByBook = [];
  const awayOddsByBook = [];
  let bestHome = { odds: null, book: "" };
  let bestAway = { odds: null, book: "" };
  const totalsByPoint = new Map();
  const spreadsByLine = new Map();

  for (const bookmaker of Array.isArray(event?.bookmakers) ? event.bookmakers : []) {
    const bookKey = normalizeBookKey(bookmaker?.key);
    if (!bookKey) continue;
    const markets = Array.isArray(bookmaker?.markets) ? bookmaker.markets : [];
    const h2h = markets.find((m) => m?.key === "h2h");
    if (h2h && Array.isArray(h2h.outcomes)) {
      const homeOutcome = h2h.outcomes.find((o) => o?.name === homeTeam);
      const awayOutcome = h2h.outcomes.find((o) => o?.name === awayTeam);
      const homeOdds = Number(homeOutcome?.price);
      const awayOdds = Number(awayOutcome?.price);
      if (Number.isFinite(homeOdds) && Number.isFinite(awayOdds)) {
        moneylineByBook[bookKey] = { home: homeOdds, away: awayOdds };
        homeOddsByBook.push(homeOdds);
        awayOddsByBook.push(awayOdds);
        if (pickBetterLine(bestHome.odds, homeOdds)) bestHome = { odds: homeOdds, book: bookKey };
        if (pickBetterLine(bestAway.odds, awayOdds)) bestAway = { odds: awayOdds, book: bookKey };
      }
    }
    const totals = markets.find((m) => m?.key === "totals");
    if (totals && Array.isArray(totals.outcomes)) {
      const over = totals.outcomes.find((o) => /over/i.test(String(o?.name || "")));
      const under = totals.outcomes.find((o) => /under/i.test(String(o?.name || "")));
      const point = Number(over?.point ?? under?.point);
      const overOdds = Number(over?.price);
      const underOdds = Number(under?.price);
      if (Number.isFinite(point) && Number.isFinite(overOdds) && Number.isFinite(underOdds)) {
        const key = point.toFixed(2);
        if (!totalsByPoint.has(key)) {
          totalsByPoint.set(key, {
            point,
            overOddsByBook: [],
            underOddsByBook: [],
            bestOver: { odds: null, book: "" },
            bestUnder: { odds: null, book: "" },
          });
        }
        const row = totalsByPoint.get(key);
        row.overOddsByBook.push(overOdds);
        row.underOddsByBook.push(underOdds);
        if (pickBetterLine(row.bestOver.odds, overOdds)) row.bestOver = { odds: overOdds, book: bookKey };
        if (pickBetterLine(row.bestUnder.odds, underOdds)) row.bestUnder = { odds: underOdds, book: bookKey };
      }
    }
    if (!isSoccer) {
      const spreads = markets.find((m) => m?.key === "spreads");
      if (spreads && Array.isArray(spreads.outcomes)) {
        const homeOutcome = spreads.outcomes.find((o) => o?.name === homeTeam);
        const awayOutcome = spreads.outcomes.find((o) => o?.name === awayTeam);
        const homePoint = Number(homeOutcome?.point);
        const awayPoint = Number(awayOutcome?.point);
        const homeOdds = Number(homeOutcome?.price);
        const awayOdds = Number(awayOutcome?.price);
        if (
          Number.isFinite(homePoint) &&
          Number.isFinite(awayPoint) &&
          Number.isFinite(homeOdds) &&
          Number.isFinite(awayOdds)
        ) {
          const key = Math.abs(homePoint).toFixed(2);
          if (!spreadsByLine.has(key)) {
            spreadsByLine.set(key, {
              line: Math.abs(homePoint),
              homePoint,
              awayPoint,
              homeOddsByBook: [],
              awayOddsByBook: [],
              bestHome: { odds: null, book: "", point: homePoint },
              bestAway: { odds: null, book: "", point: awayPoint },
            });
          }
          const row = spreadsByLine.get(key);
          row.homeOddsByBook.push(homeOdds);
          row.awayOddsByBook.push(awayOdds);
          if (pickBetterLine(row.bestHome.odds, homeOdds)) row.bestHome = { odds: homeOdds, book: bookKey, point: homePoint };
          if (pickBetterLine(row.bestAway.odds, awayOdds)) row.bestAway = { odds: awayOdds, book: bookKey, point: awayPoint };
        }
      }
    }
  }

  if (!Object.keys(moneylineByBook).length && !totalsByPoint.size && !spreadsByLine.size) return { game: null, picks: [], watchCandidates: [] };

  const game = {
    id: String(event?.id || `${sportKey}_${awayTeam}_${homeTeam}_${commenceTime}`),
    sport: sportKey,
    label: meta.label,
    emoji: meta.emoji,
    home_team: homeTeam,
    away_team: awayTeam,
    commence_time: commenceTime,
    moneyline_by_book: moneylineByBook,
    home_ml: bestHome.odds,
    away_ml: bestAway.odds,
  };

  const picks = [];
  const watchCandidates = [];
  const pushCandidate = (candidate) => {
    if (!Number.isFinite(candidate?.best?.odds) || !Number.isFinite(candidate?.fair)) return;
    const price = Number(candidate.best.odds);
    const booksCompared = Math.max(0, Number(candidate.booksCompared || 0));
    if (price < -180) return;
    if ((candidate.market === "spread" || candidate.market === "total") && booksCompared < MIN_BOOKS_FOR_SIDE_MARKET) return;
    if ((candidate.market === "spread" || candidate.market === "total") && Math.abs(price) > MAX_ODDS_FOR_SIDE_MARKET) return;
    const rawEv = expectedValuePct(price, candidate.fair);
    const ev = compressDisplayEdge(rawEv);
    if (!Number.isFinite(ev)) return;
    const confidence = calibratedConfidence(rawEv, ev);
    const row = {
      sport: sportKey,
      label: meta.label,
      emoji: meta.emoji,
      game: gameLabel,
      game_time: commenceTime,
      bet: candidate.bet,
      market: candidate.market || "",
      odds: String(price > 0 ? `+${price}` : price),
      edge: Number(ev.toFixed(2)),
      ev: Number(ev.toFixed(2)),
      confidence_calibrated: confidence,
      best_book: candidate.best.book,
      book: candidate.best.book,
      data_source: "odds_api",
      model_v2: {
        ensemble_score: Number(ev.toFixed(2)),
        timing_model: Number((ev / 10).toFixed(2)),
      },
      clv_expectation: Number((Math.max(0.1, ev * 0.18)).toFixed(2)),
      recommended_stake_pct: Number(Math.max(0.4, Math.min(2.5, ev * 0.18)).toFixed(2)),
      agents: ["Best-Line EV", "Consensus De-vig", "Market Timing"],
      active_agents: [1, 2, 5],
    };
    if (ev > 0) {
      picks.push(row);
      return;
    }
    if (boardDay === "tomorrow" && rawEv > -1.25) {
      watchCandidates.push({
        ...row,
        edge: Number(rawEv.toFixed(2)),
        ev: Number(rawEv.toFixed(2)),
        confidence_calibrated: Math.max(48, Math.min(62, confidence)),
        watch_only: true,
      });
    }
  };

  let targetTotals = null;
  for (const row of totalsByPoint.values()) {
    const cnt = Math.min(row.overOddsByBook.length, row.underOddsByBook.length);
    if (!targetTotals || cnt > targetTotals.cnt) targetTotals = { ...row, cnt };
  }
  if (targetTotals) {
    const overMedian = median(targetTotals.overOddsByBook);
    const underMedian = median(targetTotals.underOddsByBook);
    const pOverRaw = impliedProbability(overMedian);
    const pUnderRaw = impliedProbability(underMedian);
    const pSum = (pOverRaw || 0) + (pUnderRaw || 0);
    const fairOver = pSum > 0 ? pOverRaw / pSum : null;
    const fairUnder = pSum > 0 ? pUnderRaw / pSum : null;
    pushCandidate({
      bet: `Over ${formatLinePoint(targetTotals.point)}`,
      market: "total",
      best: targetTotals.bestOver,
      fair: fairOver,
      booksCompared: Math.min(targetTotals.overOddsByBook.length, targetTotals.underOddsByBook.length),
    });
    pushCandidate({
      bet: `Under ${formatLinePoint(targetTotals.point)}`,
      market: "total",
      best: targetTotals.bestUnder,
      fair: fairUnder,
      booksCompared: Math.min(targetTotals.overOddsByBook.length, targetTotals.underOddsByBook.length),
    });
  }

  if (!isSoccer) {
    const homeMedian = median(homeOddsByBook);
    const awayMedian = median(awayOddsByBook);
    const pHomeRaw = impliedProbability(homeMedian);
    const pAwayRaw = impliedProbability(awayMedian);
    const total = (pHomeRaw || 0) + (pAwayRaw || 0);
    const fairHome = total > 0 ? (pHomeRaw / total) : null;
    const fairAway = total > 0 ? (pAwayRaw / total) : null;
    pushCandidate({ bet: `${homeTeam} ML`, market: "moneyline", best: bestHome, fair: fairHome });
    pushCandidate({ bet: `${awayTeam} ML`, market: "moneyline", best: bestAway, fair: fairAway });

    let targetSpread = null;
    for (const row of spreadsByLine.values()) {
      const cnt = Math.min(row.homeOddsByBook.length, row.awayOddsByBook.length);
      if (!targetSpread || cnt > targetSpread.cnt) targetSpread = { ...row, cnt };
    }
    if (targetSpread) {
      const homeSpreadMedian = median(targetSpread.homeOddsByBook);
      const awaySpreadMedian = median(targetSpread.awayOddsByBook);
      const pHomeSpreadRaw = impliedProbability(homeSpreadMedian);
      const pAwaySpreadRaw = impliedProbability(awaySpreadMedian);
      const pSpreadSum = (pHomeSpreadRaw || 0) + (pAwaySpreadRaw || 0);
      const fairHomeSpread = pSpreadSum > 0 ? pHomeSpreadRaw / pSpreadSum : null;
      const fairAwaySpread = pSpreadSum > 0 ? pAwaySpreadRaw / pSpreadSum : null;
      pushCandidate({
        bet: `${homeTeam} ${signedLine(targetSpread.bestHome.point)}`,
        market: "spread",
        best: targetSpread.bestHome,
        fair: fairHomeSpread,
        booksCompared: Math.min(targetSpread.homeOddsByBook.length, targetSpread.awayOddsByBook.length),
      });
      pushCandidate({
        bet: `${awayTeam} ${signedLine(targetSpread.bestAway.point)}`,
        market: "spread",
        best: targetSpread.bestAway,
        fair: fairAwaySpread,
        booksCompared: Math.min(targetSpread.homeOddsByBook.length, targetSpread.awayOddsByBook.length),
      });
    }
  }

  return { game, picks, watchCandidates };
}

async function fetchSportOdds(env, sportKey, options = {}) {
  const apiKey = resolveOddsApiKey(env);
  if (!apiKey) throw new Error("ODDS_API_KEY missing in Cloudflare environment.");

  const bookCsv = bookmakersForBudget(env, Number.isFinite(options?.quotaRemaining) ? Number(options.quotaRemaining) : lastQuotaRemaining);
  const u = new URL(`https://api.the-odds-api.com/v4/sports/${encodeURIComponent(sportKey)}/odds`);
  u.searchParams.set("apiKey", apiKey);
  u.searchParams.set("regions", "us");
  const isSoccer = String(sportKey || "").toLowerCase().startsWith("soccer_");
  u.searchParams.set("markets", isSoccer ? "h2h,totals" : "h2h,spreads,totals");
  u.searchParams.set("oddsFormat", "american");
  u.searchParams.set("bookmakers", bookCsv);

  const res = await fetch(u.toString(), { method: "GET" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Odds API ${sportKey} failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const rows = await res.json();
  const remaining = Number(res.headers.get("x-requests-remaining") || 0);
  return { rows: Array.isArray(rows) ? rows : [], remaining, bookmakers: bookCsv.split(",").filter(Boolean) };
}

export async function getScanPayload(env, options = {}) {
  const now = Date.now();
  const sports = Array.isArray(options?.sports) && options.sports.length ? options.sports : DEFAULT_SPORTS;
  const boardDay = normalizeBoardDay(options?.boardDay || "today");
  const cacheKey = `scan:${boardDay}:${sports.join(",")}`;
  const force = !!options?.force;
  const maxUpcomingDays = Math.max(1, Number(env?.MAX_UPCOMING_GAME_DAYS || MAX_UPCOMING_DAYS));
  const quotaDegradeThreshold = Math.max(10, Number(env?.ODDS_DEGRADE_THRESHOLD || QUOTA_DEGRADE_THRESHOLD));
  const quotaCacheOnlyThreshold = Math.max(5, Number(env?.ODDS_CACHE_ONLY_THRESHOLD || QUOTA_CACHE_ONLY_THRESHOLD));
  const cachedEntry = cacheStore.get(cacheKey);

  if (!force && cachedEntry && (now - cachedEntry.ts) < TTL_MS) {
    return cachedEntry.payload;
  }

  if (!force && cachedEntry && Number.isFinite(lastQuotaRemaining) && lastQuotaRemaining <= quotaCacheOnlyThreshold) {
    const payload = clonePayload(cachedEntry.payload);
    payload.quota_soft_limited = true;
    payload.quota_remaining = lastQuotaRemaining;
    payload.scan_policy = {
      ...(payload.scan_policy || {}),
      served_from_cache: true,
      served_stale_cache: true,
      budget_mode: "cache_only",
      cache_ttl_seconds: Math.floor(TTL_MS / 1000),
      updated_at: Math.floor(now / 1000),
    };
    return payload;
  }

  if (!force && cachedEntry && lastEmptyScanAt && (now - lastEmptyScanAt) < EMPTY_SCAN_BACKOFF_MS) {
    const payload = clonePayload(cachedEntry.payload);
    payload.scan_policy = {
      ...(payload.scan_policy || {}),
      served_from_cache: true,
      served_stale_cache: true,
      empty_scan_backoff_seconds: Math.max(0, Math.floor((EMPTY_SCAN_BACKOFF_MS - (now - lastEmptyScanAt)) / 1000)),
      cache_ttl_seconds: Math.floor(TTL_MS / 1000),
      updated_at: Math.floor(now / 1000),
    };
    return payload;
  }

  const allGames = [];
  const allPicks = [];
  const allWatchCandidates = [];
  const fetchCounts = {};
  const fetchErrors = {};
  let quotaRemaining = Number.isFinite(lastQuotaRemaining) ? lastQuotaRemaining : null;
  const hasApiKey = !!resolveOddsApiKey(env);
  let bookmakersUsed = bookmakersForBudget(env, quotaRemaining).split(",").filter(Boolean);

  for (const sport of sports) {
    const backoffUntil = Number(sportBackoffUntil.get(sport) || 0);
    if (!force && backoffUntil > now) {
      fetchCounts[sport] = 0;
      fetchErrors[sport] = `sport_idle_backoff:${Math.ceil((backoffUntil - now) / 1000)}s`;
      continue;
    }
    let oddsPack;
    try {
      oddsPack = await fetchSportOdds(env, sport, { quotaRemaining });
    } catch (err) {
      fetchCounts[sport] = 0;
      fetchErrors[sport] = String(err?.message || err || "fetch_failed");
      if (/OUT_OF_USAGE_CREDITS|quota has been reached|401/i.test(fetchErrors[sport])) {
        quotaRemaining = 0;
        lastQuotaRemaining = 0;
      }
      continue;
    }
    bookmakersUsed = Array.isArray(oddsPack.bookmakers) && oddsPack.bookmakers.length ? oddsPack.bookmakers : bookmakersUsed;
    if (Number.isFinite(oddsPack.remaining)) quotaRemaining = oddsPack.remaining;
    const upcomingEvents = oddsPack.rows.filter((event) =>
      withinUpcomingWindow(event?.commence_time, maxUpcomingDays) && withinBoardWindow(event?.commence_time, boardDay),
    );
    fetchCounts[sport] = upcomingEvents.length;
    if (!upcomingEvents.length) {
      sportBackoffUntil.set(sport, now + SPORT_IDLE_BACKOFF_MS);
      continue;
    }
    sportBackoffUntil.delete(sport);

    for (const event of upcomingEvents) {
      const { game, picks, watchCandidates } = buildGameAndPicksFromEvent(sport, event, { boardDay });
      if (game) allGames.push(game);
      if (picks.length) allPicks.push(...picks);
      if (watchCandidates.length) allWatchCandidates.push(...watchCandidates);
    }
  }

  const filteredPicks = allPicks.filter((p) =>
    withinUpcomingWindow(p?.game_time, maxUpcomingDays) && withinBoardWindow(p?.game_time, boardDay),
  );
  const filteredWatchCandidates = allWatchCandidates
    .filter((p) => withinUpcomingWindow(p?.game_time, maxUpcomingDays) && withinBoardWindow(p?.game_time, boardDay))
    .sort((a, b) => Number(b.ev || 0) - Number(a.ev || 0));
  allGames.sort((a, b) => String(a.commence_time || "").localeCompare(String(b.commence_time || "")));
  filteredPicks.sort((a, b) => Number(b.ev || 0) - Number(a.ev || 0));

  const visible = filteredPicks.slice(0, boardDay === "tomorrow" ? 5 : 8);
  const watchBase = filteredPicks.slice(visible.length, visible.length + 8);
  const watchSeen = new Set(watchBase.map((p) => `${String(p.game || "")}|${String(p.bet || "")}|${String(p.odds || "")}`));
  const watchFill = filteredWatchCandidates.filter((p) => {
    const key = `${String(p.game || "")}|${String(p.bet || "")}|${String(p.odds || "")}`;
    if (watchSeen.has(key)) return false;
    watchSeen.add(key);
    return true;
  });
  let watchlist = watchBase.concat(watchFill.slice(0, Math.max(0, 8 - watchBase.length)));
  if (!watchlist.length && filteredPicks.length > 0) {
    const start = Math.min(3, filteredPicks.length);
    watchlist = filteredPicks.slice(start, start + 8);
  }
  const payload = {
    picks: visible,
    watchlist,
    ev_rows: evFinderRows({ picks: visible }),
    picks_total: filteredPicks.length,
    games: allGames,
    games_total: allGames.length,
    fallback_mode: false,
    debug_fetch_counts: fetchCounts,
    debug_fetch_errors: fetchErrors,
    debug_has_api_key: hasApiKey,
    quota_remaining: quotaRemaining,
    sports_covered: sports,
    scan_policy: {
      served_from_cache: false,
      cache_ttl_seconds: Math.floor(TTL_MS / 1000),
      updated_at: Math.floor(now / 1000),
      data_delay_seconds: 0,
      woke_from_idle: false,
      system_status: "AWAKE",
      bookmakers_used: bookmakersUsed,
      board_day: boardDay,
      budget_mode: Number.isFinite(quotaRemaining) && quotaRemaining <= quotaCacheOnlyThreshold
        ? "cache_only"
        : Number.isFinite(quotaRemaining) && quotaRemaining <= quotaDegradeThreshold
          ? "degraded"
          : "normal",
    },
    paywall: {
      visible_pick_limit: 3,
      next_tier: "premium",
    },
    data_source: "odds_api",
  };

  lastQuotaRemaining = Number.isFinite(quotaRemaining) ? quotaRemaining : lastQuotaRemaining;
  lastEmptyScanAt = (!filteredPicks.length && !allGames.length) ? now : 0;
  cacheStore.set(cacheKey, { ts: now, payload });
  return payload;
}

export function evFinderRows(scanPayload) {
  const rows = [];
  for (const p of Array.isArray(scanPayload?.picks) ? scanPayload.picks : []) {
    rows.push({
      bet: p.bet,
      game: p.game,
      game_time: p.game_time || "",
      ev: p.ev,
      bookOdds: p.odds,
      book: p.best_book || p.book || "",
      books_compared: 1,
    });
  }
  return rows.sort((a, b) => Number(b.ev || 0) - Number(a.ev || 0));
}
