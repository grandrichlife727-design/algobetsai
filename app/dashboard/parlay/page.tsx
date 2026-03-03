'use client'

import { useState } from 'react'

export default function ParlayPage() {
  const [legs, setLegs] = useState<any[]>([])
  const [wager, setWager] = useState(100)
  const [kellyFraction, setKellyFraction] = useState(0.25)

  const addLeg = () => {
    setLegs([
      ...legs,
      {
        id: Date.now(),
        pick: null,
        odds: -110,
        implied: 0.524
      }
    ])
  }

  const removeLeg = (id: number) => {
    setLegs(legs.filter(l => l.id !== id))
  }

  const calculateParlay = () => {
    if (legs.length === 0) return { to_win: 0, total_payout: wager, probability: 0, kelly_size: 0 }

    // Calculate parlay odds
    let decimal_odds = 1
    let probability = 1

    legs.forEach(leg => {
      const decimal = (100 / (Math.abs(leg.odds) || 110)) + 1
      decimal_odds *= decimal
      probability *= leg.implied
    })

    const parlay_odds = (decimal_odds - 1) * 100
    const to_win = (wager * decimal_odds) - wager
    const kelly_size = (wager * probability - (1 - probability)) / (decimal_odds - 1)

    return {
      to_win: Math.round(to_win),
      total_payout: Math.round(wager + to_win),
      probability: (probability * 100).toFixed(2),
      kelly_size: Math.max(0, Math.round(kelly_size * kellyFraction))
    }
  }

  const parlay = calculateParlay()

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white mb-2">Parlay Builder</h1>
        <p className="text-slate-400">Construct multi-leg parlays with correlation warnings and Kelly sizing</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Parlay Legs */}
        <div className="lg:col-span-2 space-y-4">
          {legs.length === 0 ? (
            <div className="bg-slate-900 rounded-lg border border-slate-800 p-12 text-center">
              <p className="text-slate-400 mb-4">No legs added yet</p>
              <button
                onClick={addLeg}
                className="px-4 py-2 bg-cyan-600 hover:bg-cyan-700 text-white rounded font-medium"
              >
                Add First Leg
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              {legs.map((leg, idx) => (
                <div key={leg.id} className="bg-slate-900 rounded-lg border border-slate-800 p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-white font-semibold">Leg {idx + 1}</h3>
                    <button
                      onClick={() => removeLeg(leg.id)}
                      className="text-red-400 hover:text-red-300 text-sm"
                    >
                      Remove
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <input
                      type="text"
                      placeholder="Pick (e.g., Lakers ML)"
                      className="px-3 py-2 rounded bg-slate-800 border border-slate-700 text-white text-sm"
                    />
                    <input
                      type="number"
                      placeholder="Odds (e.g., -110)"
                      value={leg.odds}
                      className="px-3 py-2 rounded bg-slate-800 border border-slate-700 text-white text-sm"
                    />
                  </div>
                </div>
              ))}
              <button
                onClick={addLeg}
                className="w-full px-4 py-2 border border-dashed border-slate-700 text-slate-400 hover:text-cyan-400 rounded font-medium"
              >
                + Add Another Leg
              </button>
            </div>
          )}
        </div>

        {/* Sizing Calculator */}
        <div className="space-y-4">
          {/* Wager */}
          <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
            <label className="block text-sm text-slate-400 mb-2">Initial Wager</label>
            <div className="flex items-center gap-2">
              <span className="text-slate-400">$</span>
              <input
                type="number"
                value={wager}
                onChange={(e) => setWager(Number(e.target.value))}
                className="flex-1 px-3 py-2 rounded bg-slate-800 border border-slate-700 text-white"
              />
            </div>
          </div>

          {/* Kelly Fraction */}
          <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
            <label className="block text-sm text-slate-400 mb-2">Kelly Fraction</label>
            <select
              value={kellyFraction}
              onChange={(e) => setKellyFraction(Number(e.target.value))}
              className="w-full px-3 py-2 rounded bg-slate-800 border border-slate-700 text-white"
            >
              <option value={0.1}>1/10 Kelly (Conservative)</option>
              <option value={0.25}>1/4 Kelly (Moderate)</option>
              <option value={0.5}>1/2 Kelly (Aggressive)</option>
              <option value={1}>Full Kelly (Max Risk)</option>
            </select>
          </div>

          {/* Results */}
          <div className="bg-slate-900 rounded-lg border border-slate-800 p-4 space-y-3">
            <div>
              <p className="text-sm text-slate-400">Win Probability</p>
              <p className="text-2xl font-bold text-cyan-400">{parlay.probability}%</p>
            </div>
            <div className="border-t border-slate-700 pt-3">
              <p className="text-sm text-slate-400">To Win</p>
              <p className="text-2xl font-bold text-emerald-400">${parlay.to_win}</p>
            </div>
            <div className="border-t border-slate-700 pt-3">
              <p className="text-sm text-slate-400">Total Payout</p>
              <p className="text-2xl font-bold text-white">${parlay.total_payout}</p>
            </div>
            <div className="border-t border-slate-700 pt-3 bg-slate-800 rounded p-3">
              <p className="text-xs text-slate-500 mb-1">Kelly Recommended Size</p>
              <p className="text-xl font-bold text-amber-400">${parlay.kelly_size}</p>
            </div>
          </div>

          {/* Warning */}
          {legs.length > 2 && (
            <div className="bg-amber-900 border border-amber-700 rounded-lg p-3 text-amber-100 text-sm">
              ⚠️ Parlays lose if any leg loses. Use conservatively.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
