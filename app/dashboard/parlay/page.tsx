'use client'

import { useState } from 'react'

export default function ParlayPage() {
  const [legs, setLegs] = useState([
    {
      id: 1,
      pick: 'Cowboys -3.5',
      odds: -110,
      kelly: 2.3,
      confidence: 87,
    },
    {
      id: 2,
      pick: 'Over 218.5',
      odds: -110,
      kelly: 1.8,
      confidence: 79,
    },
  ])

  const calculateParlay = () => {
    const totalOdds = legs.reduce((acc, leg) => {
      const decimalOdds = leg.odds > 0 ? 1 + leg.odds / 100 : 100 / Math.abs(leg.odds)
      return acc * decimalOdds
    }, 1)

    return {
      americanOdds: totalOdds > 2 ? (totalOdds - 1) * 100 : -100 / (totalOdds - 1),
      decimalOdds: totalOdds,
      impliedProb: (1 / totalOdds) * 100,
    }
  }

  const parlay = calculateParlay()

  const handleStakeChange = (stake: number) => {
    const toWin = stake * (parlay.decimalOdds - 1)
    const total = stake + toWin
    return { toWin, total }
  }

  const stake100 = handleStakeChange(100)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-slate-100">Parlay Builder</h1>
        <p className="text-slate-400 mt-1">Intelligent multi-leg betting with correlation analysis</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Legs */}
        <div className="lg:col-span-2 space-y-4">
          <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
            <h3 className="font-semibold text-slate-100 mb-4">Parlay Legs ({legs.length})</h3>
            
            <div className="space-y-3">
              {legs.map((leg, idx) => (
                <div key={leg.id} className="p-4 rounded-lg bg-slate-800/50 border border-slate-700">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <span className="w-6 h-6 rounded-full bg-cyan-500/20 text-cyan-300 flex items-center justify-center text-xs font-bold">
                        {idx + 1}
                      </span>
                      <span className="font-semibold text-slate-100">{leg.pick}</span>
                    </div>
                    <button className="text-red-400 hover:text-red-300 text-sm">Remove</button>
                  </div>
                  
                  <div className="flex items-center gap-4 text-sm text-slate-400">
                    <span>{leg.odds > 0 ? '+' : ''}{leg.odds}</span>
                    <span className="text-cyan-400 font-medium">{leg.confidence}% confidence</span>
                    <span className="text-emerald-400">Kelly: {leg.kelly.toFixed(1)}%</span>
                  </div>
                </div>
              ))}
            </div>

            <button className="w-full mt-4 px-4 py-3 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-300 hover:bg-cyan-500/30 transition-colors font-medium">
              + Add Leg
            </button>
          </div>

          {/* Correlation Analysis */}
          <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
            <h3 className="font-semibold text-slate-100 mb-4">Correlation Analysis</h3>
            
            <div className="space-y-3">
              <div className="p-3 rounded bg-slate-800/50 border-l-2 border-emerald-500">
                <p className="text-sm text-slate-300">
                  <span className="font-semibold text-emerald-400">Low Correlation</span> - Cowboys and Lakers play different sports
                </p>
              </div>
              
              <div className="text-xs text-slate-500 space-y-1">
                <p>• Negative correlation would reduce effective odds</p>
                <p>• High correlation increases risk significantly</p>
                <p>• Current parlay: <span className="text-emerald-400">Low risk profile</span></p>
              </div>
            </div>
          </div>
        </div>

        {/* Calculator */}
        <div className="space-y-4">
          <div className="rounded-lg border border-cyan-500/50 bg-cyan-500/10 p-6">
            <h3 className="font-semibold text-slate-100 mb-4">Parlay Odds</h3>
            
            <div className="space-y-3 mb-6">
              <div>
                <p className="text-xs text-slate-400 mb-1">American Odds</p>
                <p className="text-2xl font-bold text-cyan-400">
                  {parlay.americanOdds > 0 ? '+' : ''}{Math.round(parlay.americanOdds)}
                </p>
              </div>
              
              <div>
                <p className="text-xs text-slate-400 mb-1">Decimal Odds</p>
                <p className="text-2xl font-bold text-slate-100">{parlay.decimalOdds.toFixed(2)}x</p>
              </div>
              
              <div>
                <p className="text-xs text-slate-400 mb-1">Implied Probability</p>
                <p className="text-2xl font-bold text-amber-400">{parlay.impliedProb.toFixed(1)}%</p>
              </div>
            </div>

            <div className="border-t border-cyan-500/30 pt-4">
              <p className="text-xs text-slate-400 mb-3">Stake: $100</p>
              
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-300">To Win:</span>
                  <span className="font-semibold text-emerald-400">${stake100.toWin.toFixed(0)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-300">Total Return:</span>
                  <span className="text-lg font-bold text-emerald-400">${stake100.total.toFixed(0)}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Kelly Recommendation */}
          <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
            <h3 className="font-semibold text-slate-100 mb-3">Kelly Sizing</h3>
            
            <div className="space-y-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Full Kelly:</span>
                <span className="font-semibold text-cyan-400">2.1%</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-400">¼ Kelly (Safe):</span>
                <span className="font-semibold text-emerald-400">0.5%</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-400">½ Kelly (Balanced):</span>
                <span className="font-semibold text-amber-400">1.1%</span>
              </div>
            </div>

            <p className="text-xs text-slate-500 mt-4">
              Recommended: ¼ Kelly ($50 on $10,000 bankroll) for conservative approach
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
