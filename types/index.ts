export interface Pick {
  id: string
  sport: 'NFL' | 'NBA' | 'MLB' | 'NHL' | 'CFB' | 'CBB' | 'WNBA' | 'MLS'
  event: string
  pick: string
  confidence: number
  edgePercent: number
  currentOdds: number
  impliedProb: number
  recommendedUnits: number
  kellyPercent: number
  quarterKellyPercent: number
  signalBreakdown: {
    clvEdge: number
    sharpMoney: number
    lineMovement: number
    consensus: number
    oddsQuality: number
    injuryNews: number
  }
  odds: {
    pinnacle?: number
    draftkings?: number
    fanduel?: number
    betmgm?: number
    caesars?: number
  }
  status: 'pending' | 'won' | 'lost' | 'push' | 'void'
  closingLineValue?: number
  timestamp: number
  notes?: string
}

export interface Alert {
  id: string
  type: 'steam' | 'rlm' | 'odds_boost' | 'line_move' | 'sharp_move'
  title: string
  description: string
  sport: string
  event: string
  percentChange: number
  timestamp: number
  read: boolean
}

export interface PerformanceStats {
  totalPicks: number
  wins: number
  losses: number
  pushes: number
  winRate: number
  roi: number
  sharpeRatio: number
  avgConfidence: number
  totalUnits: number
  profitUnits: number
  clvAverage: number
}

export interface ParleyLeg {
  pickId: string
  odds: number
  correlationRisk: 'low' | 'medium' | 'high'
}

export interface Parley {
  id: string
  legs: ParleyLeg[]
  totalOdds: number
  recommendation: number
  kellyRecommendation: number
  quarterKellyRecommendation: number
  expectedValue: number
}

export interface User {
  id: string
  email: string
  username: string
  preferredTheme: 'dark' | 'light'
  bankroll: number
  riskTolerance: 'conservative' | 'moderate' | 'aggressive'
  createdAt: number
}
