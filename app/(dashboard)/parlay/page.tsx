'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/lib/api'
import { generateMockPicks, calculateExpectedValue, calculateKellyPercent } from '@/lib/algorithms'
import { formatOdds, formatPercent, formatCurrency, calculateDecimalOdds, getSportEmoji } from '@/lib/utils'

export default function ParlayBuilderPage() {
  const [selectedLegs, setSelectedLegs] = useState<string[]>([])
  const [stakeAmount, setStakeAmount] = useState(100)

  // Fetch picks
  const { data: picks = [] } = useQuery({
    queryKey: ['picks', 'parlay'],
    queryFn: async () => {
      try {
        const result = await api.getPicks({ limit: 30 })
        return result.data || generateMockPicks(20)
      } catch {
        return generateMockPicks(20)
      }
    },
  })

  // Selected legs data
  const parlayLegs = picks.filter((p: any) => selectedLegs.includes(p.id))

  // Calculate parlay stats
  const parlayStats = {
    totalOdds: parlayLegs.reduce((acc: number, leg: any) => {
      const decimal = calculateDecimalOdds(leg.currentOdds)
      return acc * decimal
    }, 1),
    totalProfit: 0,
    combinedConfidence: parlayLegs.length
      ? Math.round(
          parlayLegs.reduce((sum: number, p: any) => sum + p.confidence, 0) /
            parlayLegs.length
        )
      : 0,
    correlationRisk: assessCorrelationRisk(parlayLegs),
  }

  parlayStats.totalProfit = Math.round((parlayStats.totalOdds - 1) * stakeAmount)

  const kelly = calculateKellyPercent(
    parlayStats.combinedConfidence,
    100 * (parlayStats.totalOdds - 1),
    10000
  )

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-foreground">Parlay Builder</h1>
        <p className="text-muted-foreground">
          Combine picks with intelligent correlation analysis and kelly-adjusted sizing
        </p>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        {/* Left - Pick Selection */}
        <div className="lg:col-span-2 space-y-4">
          <div className="space-y-3">
            <h2 className="text-lg font-semibold text-foreground">Available Picks</h2>
            <p className="text-sm text-muted-foreground">
              Select 2+ picks to build a parlay
            </p>

            <div className="space-y-2 max-h-96 overflow-auto">
              {picks
                .filter((p: any) => p.status === 'pending')
                .map((pick: any) => (
                  <button
                    key={pick.id}
                    onClick={() => {
                      setSelectedLegs((prev) =>
                        prev.includes(pick.id)
                          ? prev.filter((id) => id !== pick.id)
                          : [...prev, pick.id]
                      )
                    }}
                    className={`w-full rounded-lg border p-4 text-left transition-all ${
                      selectedLegs.includes(pick.id)
                        ? 'border-primary bg-primary/10'
                        : 'border-muted bg-muted/50 hover:border-primary/50'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-lg">{getSportEmoji(pick.sport)}</span>
                          <div>
                            <p className="font-medium text-foreground">{pick.pick}</p>
                            <p className="text-sm text-muted-foreground">{pick.event}</p>
                          </div>
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="font-bold text-primary">{pick.confidence}%</div>
                        <div className="text-sm text-muted-foreground">
                          {formatOdds(pick.currentOdds)}
                        </div>
                      </div>
                    </div>
                  </button>
                ))}
            </div>
          </div>

          {/* Selected Legs Details */}
          {parlayLegs.length > 0 && (
            <div className="rounded-lg border border-muted bg-muted/50 p-4 space-y-4">
              <h3 className="font-semibold text-foreground">Parlay Legs</h3>
              <div className="space-y-2">
                {parlayLegs.map((leg: any, idx: number) => (
                  <div key={leg.id} className="flex items-center justify-between p-3 rounded bg-muted/50">
                    <div>
                      <div className="text-sm font-medium text-foreground">
                        Leg {idx + 1}: {leg.pick}
                      </div>
                      <div className="text-xs text-muted-foreground">{leg.event}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-bold text-foreground">
                        {formatOdds(leg.currentOdds)}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {calculateDecimalOdds(leg.currentOdds).toFixed(2)}x
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right - Parlay Summary */}
        <div className="space-y-4">
          <div className="rounded-lg border border-muted bg-muted/50 p-6 space-y-6 sticky top-20">
            <h3 className="font-semibold text-foreground">Parlay Summary</h3>

            {/* Stats */}
            {parlayLegs.length > 0 ? (
              <>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Legs</span>
                    <span className="font-bold text-foreground">{parlayLegs.length}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Combined Odds</span>
                    <span className="font-bold text-primary">
                      {parlayStats.totalOdds.toFixed(2)}x
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Avg Confidence</span>
                    <span className="font-bold text-foreground">
                      {parlayStats.combinedConfidence}%
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Correlation Risk</span>
                    <span
                      className={`text-sm font-bold ${
                        parlayStats.correlationRisk === 'high'
                          ? 'text-danger'
                          : parlayStats.correlationRisk === 'medium'
                            ? 'text-warning'
                            : 'text-success'
                      }`}
                    >
                      {parlayStats.correlationRisk}
                    </span>
                  </div>
                </div>

                <div className="border-t border-muted pt-4 space-y-3">
                  {/* Stake Input */}
                  <div>
                    <label className="text-sm text-muted-foreground block mb-2">
                      Stake Amount
                    </label>
                    <div className="flex gap-2">
                      <input
                        type="number"
                        min="1"
                        value={stakeAmount}
                        onChange={(e) => setStakeAmount(Math.max(1, parseInt(e.target.value) || 0))}
                        className="flex-1 px-3 py-2 rounded-lg border border-muted bg-muted/50 text-foreground focus:outline-none focus:border-primary"
                      />
                      <span className="flex items-center text-muted-foreground">$</span>
                    </div>
                  </div>

                  {/* Profit */}
                  <div className="bg-success/10 rounded-lg p-4 border border-success/30">
                    <div className="text-sm text-muted-foreground mb-1">Potential Profit</div>
                    <div className="text-2xl font-bold text-success">
                      {formatCurrency(parlayStats.totalProfit)}
                    </div>
                    <div className="text-xs text-success/70 mt-1">
                      {((parlayStats.totalProfit / stakeAmount) * 100).toFixed(0)}% ROI
                    </div>
                  </div>

                  {/* Kelly Recommendation */}
                  <div className="bg-primary/10 rounded-lg p-4 border border-primary/30">
                    <div className="text-sm text-muted-foreground mb-2">
                      Recommended Stake (¼ Kelly)
                    </div>
                    <div className="text-xl font-bold text-primary">
                      {formatCurrency(kelly.recommendedUnits)}
                    </div>
                    <div className="text-xs text-primary/70 mt-1">
                      {kelly.quarterKelly}% of bankroll
                    </div>
                  </div>
                </div>

                <button className="w-full btn-primary">Place Parlay 🎲</button>
              </>
            ) : (
              <div className="text-center py-8">
                <div className="text-4xl mb-4">🎲</div>
                <p className="text-muted-foreground text-sm">
                  Select at least 2 picks to create a parlay
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function assessCorrelationRisk(picks: any[]): 'low' | 'medium' | 'high' {
  if (picks.length < 2) return 'low'

  // Simple correlation assessment based on sport grouping
  const sportCounts: Record<string, number> = {}
  picks.forEach((p) => {
    sportCounts[p.sport] = (sportCounts[p.sport] || 0) + 1
  })

  const maxSameSport = Math.max(...Object.values(sportCounts))
  if (maxSameSport >= picks.length * 0.7) return 'high'
  if (maxSameSport >= picks.length * 0.5) return 'medium'
  return 'low'
}
