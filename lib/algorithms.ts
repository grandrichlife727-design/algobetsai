import { Pick } from '@/types'

/**
 * Advanced Multi-Factor Prediction Algorithm
 * Combines 6 weighted signals for improved edge detection
 */

export interface SignalInput {
  clvEdgeScore: number // 0-100, higher = better edge
  sharpMoneyScore: number // 0-100, higher = sharp agreement
  lineMovementScore: number // 0-100, higher = significant movement
  consensusScore: number // 0-100, higher = broad agreement
  oddsQualityScore: number // 0-100, higher = better odds available
  injuryNewsScore: number // 0-100, higher = recent positive news
}

const SIGNAL_WEIGHTS = {
  clvEdge: 0.25,
  sharpMoney: 0.20,
  lineMovement: 0.20,
  consensus: 0.15,
  oddsQuality: 0.10,
  injuryNews: 0.10,
}

/**
 * Calculate confidence based on multi-factor signals
 * Returns 52-99% confidence range
 */
export function calculateConfidence(signals: SignalInput): {
  confidence: number
  breakdown: Record<string, number>
} {
  const breakdown = {
    clvEdge: signals.clvEdgeScore * SIGNAL_WEIGHTS.clvEdge,
    sharpMoney: signals.sharpMoneyScore * SIGNAL_WEIGHTS.sharpMoney,
    lineMovement: signals.lineMovementScore * SIGNAL_WEIGHTS.lineMovement,
    consensus: signals.consensusScore * SIGNAL_WEIGHTS.consensus,
    oddsQuality: signals.oddsQualityScore * SIGNAL_WEIGHTS.oddsQuality,
    injuryNews: signals.injuryNewsScore * SIGNAL_WEIGHTS.injuryNews,
  }

  // Weighted sum (0-100)
  const weightedScore = Object.values(breakdown).reduce((a, b) => a + b, 0)

  // Map to 52-99% range (avoid false precision at extremes)
  const confidence = 52 + (weightedScore / 100) * 47

  return {
    confidence: Math.min(99, Math.max(52, Math.round(confidence))),
    breakdown: {
      clvEdge: Math.round(signals.clvEdgeScore),
      sharpMoney: Math.round(signals.sharpMoneyScore),
      lineMovement: Math.round(signals.lineMovementScore),
      consensus: Math.round(signals.consensusScore),
      oddsQuality: Math.round(signals.oddsQualityScore),
      injuryNews: Math.round(signals.injuryNewsScore),
    },
  }
}

/**
 * Calculate implied probability from American odds
 */
export function impliedProbabilityFromOdds(americanOdds: number): number {
  if (americanOdds > 0) {
    return (100 / (americanOdds + 100)) * 100
  } else {
    return (-americanOdds / (-americanOdds + 100)) * 100
  }
}

/**
 * Convert implied probability to American odds
 */
export function oddsFromProbability(probability: number): number {
  const prob = probability / 100
  if (prob > 0.5) {
    return (-prob / (1 - prob)) * 100
  } else {
    return ((1 - prob) / prob) * 100
  }
}

/**
 * Calculate Kelly Criterion stake sizing
 * f* = (bp - q) / b
 * b = odds / 100 (for American odds conversion)
 * p = win probability
 * q = loss probability (1 - p)
 */
export function calculateKellyPercent(
  winProbability: number,
  americanOdds: number,
  bankroll: number
): {
  fullKelly: number
  quarterKelly: number
  recommendedUnits: number
} {
  const p = winProbability / 100
  const q = 1 - p

  // Convert American odds to decimal
  let b: number
  if (americanOdds > 0) {
    b = americanOdds / 100
  } else {
    b = 100 / Math.abs(americanOdds)
  }

  // Kelly formula
  const f = (b * p - q) / b

  // Ensure positive Kelly (edge exists)
  const fullKelly = Math.max(0, f * 100)
  const quarterKelly = fullKelly / 4

  // Recommended units (1 unit = 1% of bankroll)
  const recommendedUnits = Math.round((quarterKelly / 100) * bankroll)

  return {
    fullKelly: Math.round(fullKelly * 100) / 100,
    quarterKelly: Math.round(quarterKelly * 100) / 100,
    recommendedUnits: Math.max(1, recommendedUnits),
  }
}

/**
 * Calculate expected value (EV) of a bet
 * EV = (win probability * payout) - (loss probability * stake)
 */
export function calculateExpectedValue(
  winProbability: number,
  odds: number,
  stake: number
): number {
  const p = winProbability / 100
  const q = 1 - p

  let payout: number
  if (odds > 0) {
    payout = stake + (stake * odds) / 100
  } else {
    payout = stake + stake / (Math.abs(odds) / 100)
  }

  const ev = p * payout - q * stake
  return Math.round(ev * 100) / 100
}

/**
 * Generate realistic mock picks for demo purposes
 */
export function generateMockPicks(count: number = 15): Partial<Pick>[] {
  const sports = ['NFL', 'NBA', 'MLB', 'NHL', 'CFB', 'CBB']
  const teams = {
    NFL: ['Chiefs', 'Eagles', 'Bills', 'Ravens', '49ers'],
    NBA: ['Celtics', 'Nuggets', 'Lakers', 'Heat', 'Suns'],
    MLB: ['Yankees', 'Dodgers', 'Braves', 'Astros', 'Mets'],
    NHL: ['Hurricanes', 'Maple Leafs', 'Avalanche', 'Vegas', 'Bruins'],
    CFB: ['Georgia', 'Alabama', 'Ohio State', 'Texas', 'Notre Dame'],
    CBB: ['Duke', 'North Carolina', 'Kansas', 'UCLA', 'Gonzaga'],
  }

  const picks = []

  for (let i = 0; i < count; i++) {
    const sport = sports[Math.floor(Math.random() * sports.length)] as any
    const team = teams[sport as keyof typeof teams][
      Math.floor(Math.random() * teams[sport as keyof typeof teams].length)
    ]
    const opponent = teams[sport as keyof typeof teams][
      Math.floor(Math.random() * teams[sport as keyof typeof teams].length)
    ]
    const pickType = ['Over', 'Under', 'Spread', 'ML'][Math.floor(Math.random() * 4)]
    const odds = [-110, -120, -105, 110, 120][Math.floor(Math.random() * 5)]

    const signals: SignalInput = {
      clvEdgeScore: Math.random() * 100,
      sharpMoneyScore: Math.random() * 100,
      lineMovementScore: Math.random() * 100,
      consensusScore: Math.random() * 100,
      oddsQualityScore: Math.random() * 100,
      injuryNewsScore: Math.random() * 100,
    }

    const { confidence, breakdown } = calculateConfidence(signals)
    const impliedProb = impliedProbabilityFromOdds(odds)
    const kelly = calculateKellyPercent(confidence, odds, 5000)
    const edge = confidence - impliedProb

    picks.push({
      id: `pick-${i + 1}`,
      sport,
      event: `${team} vs ${opponent}`,
      pick: `${team} ${pickType}`,
      confidence,
      edgePercent: Math.round(edge * 100) / 100,
      currentOdds: odds,
      impliedProb: Math.round(impliedProb * 100) / 100,
      recommendedUnits: kelly.recommendedUnits,
      kellyPercent: kelly.fullKelly,
      quarterKellyPercent: kelly.quarterKelly,
      signalBreakdown: breakdown as any,
      odds: {
        pinnacle: odds,
        draftkings: odds - 5,
        fanduel: odds + 5,
      },
      status: 'pending',
      timestamp: Date.now() - Math.random() * 86400000,
      notes: 'Strong CLV edge detected',
    })
  }

  return picks
}

/**
 * Calculate Sharpe ratio for performance evaluation
 * Sharpe Ratio = (Return - Risk Free Rate) / Standard Deviation
 */
export function calculateSharpeRatio(
  returns: number[],
  riskFreeRate: number = 0.02
): number {
  if (returns.length < 2) return 0

  const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length
  const variance =
    returns.reduce((sum, ret) => sum + Math.pow(ret - avgReturn, 2), 0) / returns.length
  const stdDev = Math.sqrt(variance)

  if (stdDev === 0) return 0

  return (avgReturn - riskFreeRate) / stdDev
}

/**
 * Calculate Closing Line Value (CLV)
 * Measures how your pick compares to the closing odds
 */
export function calculateCLV(pickOdds: number, closingOdds: number): number {
  const pickProb = impliedProbabilityFromOdds(pickOdds)
  const closingProb = impliedProbabilityFromOdds(closingOdds)
  return closingProb - pickProb
}
