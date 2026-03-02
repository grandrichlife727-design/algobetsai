const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://algobetsai.onrender.com'

// ---------- Mock data generator ----------
function generateMockPicks() {
  const sports = ['NFL', 'NBA', 'MLB', 'NHL'] as const
  const teams: Record<string, string[][]> = {
    NFL: [['Chiefs', 'Eagles'], ['Bills', '49ers'], ['Ravens', 'Cowboys'], ['Lions', 'Packers']],
    NBA: [['Celtics', 'Nuggets'], ['Lakers', 'Bucks'], ['Suns', 'Heat'], ['Knicks', 'Mavericks']],
    MLB: [['Dodgers', 'Yankees'], ['Braves', 'Astros'], ['Rangers', 'Phillies'], ['Padres', 'Orioles']],
    NHL: [['Panthers', 'Oilers'], ['Bruins', 'Avalanche'], ['Rangers', 'Stars'], ['Hurricanes', 'Jets']],
  }
  const betTypes = ['Spread', 'Moneyline', 'Over', 'Under']

  return Array.from({ length: 20 }, (_, i) => {
    const sport = sports[i % sports.length]
    const matchup = teams[sport][i % teams[sport].length]
    const betType = betTypes[Math.floor(Math.random() * betTypes.length)]
    const confidence = Math.round(55 + Math.random() * 40)
    const edge = +(1 + Math.random() * 12).toFixed(1)
    const americanOdds = Math.random() > 0.5
      ? Math.round(100 + Math.random() * 200)
      : -Math.round(100 + Math.random() * 200)

    const clvEdge = Math.round(40 + Math.random() * 60)
    const sharpMoney = Math.round(30 + Math.random() * 70)
    const lineMovement = Math.round(20 + Math.random() * 80)
    const consensus = Math.round(30 + Math.random() * 70)
    const oddsQuality = Math.round(40 + Math.random() * 60)
    const injuryNews = Math.round(20 + Math.random() * 80)

    return {
      id: `pick-${i + 1}`,
      sport,
      event: `${matchup[0]} vs ${matchup[1]}`,
      pick: `${matchup[Math.random() > 0.5 ? 0 : 1]} ${betType}${betType === 'Spread' ? ` ${Math.random() > 0.5 ? '-' : '+'}${(Math.random() * 7 + 1).toFixed(1)}` : betType.startsWith('O') || betType.startsWith('U') ? ` ${(Math.random() * 40 + 180).toFixed(1)}` : ''}`,
      confidence,
      edgePercent: edge,
      currentOdds: americanOdds,
      impliedProb: americanOdds > 0
        ? +(100 / (americanOdds + 100) * 100).toFixed(1)
        : +(Math.abs(americanOdds) / (Math.abs(americanOdds) + 100) * 100).toFixed(1),
      recommendedUnits: +(confidence >= 80 ? 2 + Math.random() : confidence >= 70 ? 1 + Math.random() : 0.5 + Math.random() * 0.5).toFixed(2),
      kellyPercent: +(edge * 0.4).toFixed(2),
      quarterKellyPercent: +(edge * 0.1).toFixed(2),
      signalBreakdown: {
        clvEdge,
        sharpMoney,
        lineMovement,
        consensus,
        oddsQuality,
        injuryNews,
      },
      odds: {
        pinnacle: americanOdds + Math.round((Math.random() - 0.5) * 10),
        draftkings: americanOdds + Math.round((Math.random() - 0.5) * 20),
        fanduel: americanOdds + Math.round((Math.random() - 0.5) * 15),
        betmgm: americanOdds + Math.round((Math.random() - 0.5) * 25),
        caesars: americanOdds + Math.round((Math.random() - 0.5) * 18),
      },
      status: 'pending' as const,
      timestamp: Date.now() - Math.random() * 86400000,
      notes: confidence >= 80 ? 'Strong edge detected across multiple signals' : undefined,
    }
  }).sort((a, b) => b.confidence - a.confidence)
}

function generateMockAlerts() {
  const types = ['steam', 'rlm', 'odds_boost', 'line_move', 'sharp_move'] as const
  const titles: Record<string, string[]> = {
    steam: ['Steam Move Detected', 'Sharp Steam Alert', 'Heavy Steam Incoming'],
    rlm: ['Reverse Line Movement', 'RLM Signal', 'Line Moving Against Public'],
    odds_boost: ['Odds Boost Available', 'Enhanced Odds', 'Boosted Line'],
    line_move: ['Significant Line Move', 'Line Shift Alert', 'Major Movement'],
    sharp_move: ['Sharp Money Alert', 'Pro Action Detected', 'Wiseguy Move'],
  }

  return Array.from({ length: 15 }, (_, i) => {
    const type = types[i % types.length]
    return {
      id: `alert-${i + 1}`,
      type,
      title: titles[type][i % titles[type].length],
      description: `${type === 'steam' ? 'Heavy one-way action' : type === 'rlm' ? 'Line moving opposite to public' : type === 'odds_boost' ? 'Enhanced odds available' : type === 'line_move' ? 'Significant shift in odds' : 'Professional bettors loading up'} - Monitor closely for value.`,
      sport: ['NFL', 'NBA', 'MLB', 'NHL'][i % 4],
      event: ['Chiefs vs Eagles', 'Celtics vs Nuggets', 'Dodgers vs Yankees', 'Panthers vs Oilers'][i % 4],
      percentChange: +(Math.random() * 8 + 1).toFixed(1),
      timestamp: Date.now() - Math.random() * 3600000 * 6,
      read: i > 5,
    }
  })
}

// Cached mock data (stays consistent within session)
let cachedPicks: ReturnType<typeof generateMockPicks> | null = null
let cachedAlerts: ReturnType<typeof generateMockAlerts> | null = null

function getMockPicks() {
  if (!cachedPicks) cachedPicks = generateMockPicks()
  return cachedPicks
}

function getMockAlerts() {
  if (!cachedAlerts) cachedAlerts = generateMockAlerts()
  return cachedAlerts
}

// ---------- API functions with mock fallback ----------

async function apiFetch<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`${API_BASE_URL}${path}`, { next: { revalidate: 0 } })
    if (!res.ok) throw new Error(`${res.status}`)
    const data = await res.json()
    // If the response looks like it has data, return it
    if (data && (Array.isArray(data) || (typeof data === 'object' && Object.keys(data).length > 0))) {
      return data as T
    }
    return fallback
  } catch {
    return fallback
  }
}

export async function fetchPicks() {
  return apiFetch('/api/picks', getMockPicks())
}

export async function fetchAlerts(limit?: number) {
  const alerts = await apiFetch('/api/alerts', getMockAlerts())
  return limit ? alerts.slice(0, limit) : alerts
}

export async function fetchPerformanceStats() {
  return apiFetch('/api/performance', {
    totalPicks: 247,
    wins: 142,
    losses: 96,
    pushes: 9,
    winRate: 59.6,
    roi: 8.7,
    sharpeRatio: 1.42,
    avgConfidence: 71.3,
    totalUnits: 312.5,
    profitUnits: 27.2,
    clvAverage: 2.1,
  })
}

export const api = {
  getPicks: fetchPicks,
  getAlerts: fetchAlerts,
  getPerformanceStats: fetchPerformanceStats,
}

export default api
