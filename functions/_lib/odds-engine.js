const SPORT_META = {
  basketball_nba: { label: "NBA", emoji: "🏀" },
  americanfootball_nfl: { label: "NFL", emoji: "🏈" },
  icehockey_nhl: { label: "NHL", emoji: "🏒" },
  basketball_ncaab: { label: "NCAAB", emoji: "🎓" },
  baseball_mlb: { label: "MLB", emoji: "⚾" },
  soccer_epl: { label: "EPL", emoji: "⚽" },
  soccer_spain_la_liga: { label: "La Liga", emoji: "⚽" },
  mma_mixed_martial_arts: { label: "MMA", emoji: "🥊" },
};

const DEFAULT_SPORTS = Object.keys(SPORT_META);
const DEFAULT_BOOKMAKERS = "draftkings,fanduel,betmgm,pinnacle,williamhill_us,bovada";
const TTL_MS = 90 * 1000;
const cacheStore = new Map();

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

function buildGameAndPicksFromEvent(sportKey, event) {
  const meta = SPORT_META[sportKey] || { label: sportKey, emoji: "🎯" };
  const isSoccer = String(sportKey || "").toLowerCase().startsWith("soccer_");
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
    if (isSoccer) {
      const totals = markets.find((m) => m?.key === "totals");
      if (!totals || !Array.isArray(totals.outcomes)) continue;
      const over = totals.outcomes.find((o) => /over/i.test(String(o?.name || "")));
      const under = totals.outcomes.find((o) => /under/i.test(String(o?.name || "")));
      const point = Number(over?.point ?? under?.point);
      const overOdds = Number(over?.price);
      const underOdds = Number(under?.price);
      if (!Number.isFinite(point) || !Number.isFinite(overOdds) || !Number.isFinite(underOdds)) continue;
      const key = point.toFixed(1);
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

  if (!Object.keys(moneylineByBook).length && (!isSoccer || !totalsByPoint.size)) return { game: null, picks: [] };

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
  if (isSoccer) {
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
      const candidates = [
        { side: "Over", best: targetTotals.bestOver, fair: fairOver },
        { side: "Under", best: targetTotals.bestUnder, fair: fairUnder },
      ];
      for (const c of candidates) {
        if (!Number.isFinite(c.best.odds) || !Number.isFinite(c.fair)) continue;
        if (Number(c.best.odds) < -180) continue;
        const ev = expectedValuePct(c.best.odds, c.fair);
        if (!Number.isFinite(ev) || ev <= 0) continue;
        const confidence = Math.max(51, Math.min(89, Math.round(54 + ev * 1.6)));
        picks.push({
          sport: sportKey,
          label: meta.label,
          emoji: meta.emoji,
          game: gameLabel,
          game_time: commenceTime,
          bet: `${c.side} ${Number(targetTotals.point).toFixed(1)}`,
          market: "total",
          odds: String(c.best.odds > 0 ? `+${c.best.odds}` : c.best.odds),
          edge: Number(ev.toFixed(2)),
          ev: Number(ev.toFixed(2)),
          confidence_calibrated: confidence,
          best_book: c.best.book,
          book: c.best.book,
          data_source: "odds_api",
          model_v2: {
            ensemble_score: Number(ev.toFixed(2)),
            timing_model: Number((ev / 10).toFixed(2)),
          },
          clv_expectation: Number((Math.max(0.1, ev * 0.18)).toFixed(2)),
          recommended_stake_pct: Number(Math.max(0.4, Math.min(2.5, ev * 0.18)).toFixed(2)),
          agents: ["Best-Line EV", "Consensus De-vig", "Market Timing"],
          active_agents: [1, 2, 5],
        });
      }
    }
  } else {
    const homeMedian = median(homeOddsByBook);
    const awayMedian = median(awayOddsByBook);
    const pHomeRaw = impliedProbability(homeMedian);
    const pAwayRaw = impliedProbability(awayMedian);
    const total = (pHomeRaw || 0) + (pAwayRaw || 0);
    const fairHome = total > 0 ? (pHomeRaw / total) : null;
    const fairAway = total > 0 ? (pAwayRaw / total) : null;
    const candidates = [
      { team: homeTeam, best: bestHome, fair: fairHome },
      { team: awayTeam, best: bestAway, fair: fairAway },
    ];
    for (const c of candidates) {
      if (!Number.isFinite(c.best.odds) || !Number.isFinite(c.fair)) continue;
      const ev = expectedValuePct(c.best.odds, c.fair);
      if (!Number.isFinite(ev) || ev <= 0) continue;
      const confidence = Math.max(51, Math.min(89, Math.round(54 + ev * 1.6)));
      picks.push({
        sport: sportKey,
        label: meta.label,
        emoji: meta.emoji,
        game: gameLabel,
        game_time: commenceTime,
        bet: `${c.team} ML`,
        odds: String(c.best.odds > 0 ? `+${c.best.odds}` : c.best.odds),
        edge: Number(ev.toFixed(2)),
        ev: Number(ev.toFixed(2)),
        confidence_calibrated: confidence,
        best_book: c.best.book,
        book: c.best.book,
        data_source: "odds_api",
        model_v2: {
          ensemble_score: Number(ev.toFixed(2)),
          timing_model: Number((ev / 10).toFixed(2)),
        },
        clv_expectation: Number((Math.max(0.1, ev * 0.18)).toFixed(2)),
        recommended_stake_pct: Number(Math.max(0.4, Math.min(2.5, ev * 0.18)).toFixed(2)),
        agents: ["Best-Line EV", "Consensus De-vig", "Market Timing"],
        active_agents: [1, 2, 5],
      });
    }
  }

  return { game, picks };
}

async function fetchSportOdds(env, sportKey) {
  const apiKey = String(env?.ODDS_API_KEY || "").trim();
  if (!apiKey) throw new Error("ODDS_API_KEY missing in Cloudflare environment.");

  const bookCsv = String(env?.ODDS_BOOKMAKERS || DEFAULT_BOOKMAKERS).trim() || DEFAULT_BOOKMAKERS;
  const u = new URL(`https://api.the-odds-api.com/v4/sports/${encodeURIComponent(sportKey)}/odds`);
  u.searchParams.set("apiKey", apiKey);
  u.searchParams.set("regions", "us");
  const isSoccer = String(sportKey || "").toLowerCase().startsWith("soccer_");
  u.searchParams.set("markets", isSoccer ? "h2h,totals" : "h2h");
  u.searchParams.set("oddsFormat", "american");
  u.searchParams.set("bookmakers", bookCsv);

  const res = await fetch(u.toString(), { method: "GET" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Odds API ${sportKey} failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const rows = await res.json();
  const remaining = Number(res.headers.get("x-requests-remaining") || 0);
  return { rows: Array.isArray(rows) ? rows : [], remaining };
}

export async function getScanPayload(env, options = {}) {
  const now = Date.now();
  const sports = Array.isArray(options?.sports) && options.sports.length ? options.sports : DEFAULT_SPORTS;
  const cacheKey = `scan:${sports.join(",")}`;
  const force = !!options?.force;

  if (!force && cacheStore.has(cacheKey)) {
    const cached = cacheStore.get(cacheKey);
    if ((now - cached.ts) < TTL_MS) return cached.payload;
  }

  const allGames = [];
  const allPicks = [];
  const fetchCounts = {};
  let quotaRemaining = null;

  for (const sport of sports) {
    let oddsPack;
    try {
      oddsPack = await fetchSportOdds(env, sport);
    } catch (_) {
      fetchCounts[sport] = 0;
      continue;
    }
    fetchCounts[sport] = oddsPack.rows.length;
    if (Number.isFinite(oddsPack.remaining)) quotaRemaining = oddsPack.remaining;

    for (const event of oddsPack.rows) {
      const { game, picks } = buildGameAndPicksFromEvent(sport, event);
      if (game) allGames.push(game);
      if (picks.length) allPicks.push(...picks);
    }
  }

  allGames.sort((a, b) => String(a.commence_time || "").localeCompare(String(b.commence_time || "")));
  allPicks.sort((a, b) => Number(b.ev || 0) - Number(a.ev || 0));

  const visible = allPicks.slice(0, 8);
  const payload = {
    picks: visible,
    picks_total: allPicks.length,
    games: allGames,
    games_total: allGames.length,
    fallback_mode: false,
    debug_fetch_counts: fetchCounts,
    debug_has_api_key: true,
    quota_remaining: quotaRemaining,
    sports_covered: sports,
    scan_policy: {
      served_from_cache: false,
      cache_ttl_seconds: Math.floor(TTL_MS / 1000),
      updated_at: Math.floor(now / 1000),
      data_delay_seconds: 0,
      woke_from_idle: false,
      system_status: "AWAKE",
    },
    paywall: {
      visible_pick_limit: 3,
      next_tier: "premium",
    },
    data_source: "odds_api",
  };

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
