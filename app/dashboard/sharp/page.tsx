'use client'

import { useState } from 'react'

export default function SharpPage() {
  const [weights, setWeights] = useState({
    clvEdge: 25,
    sharpMoney: 20,
    lineMovement: 20,
    consensus: 15,
    oddsQuality: 10,
    injuryNews: 10,
  })

  const handleWeightChange = (key: string, value: number) => {
    setWeights(prev => ({ ...prev, [key]: value }))
  }

  const total = Object.values(weights).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-slate-100">Sharp Tools</h1>
        <p className="text-slate-400 mt-1">Advanced customization for professional bettors</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Model Weights */}
        <div className="lg:col-span-2">
          <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
            <div className="flex items-center justify-between mb-6">
              <h3 className="font-semibold text-slate-100">Model Weights</h3>
              <span className={`text-sm font-bold ${total === 100 ? 'text-emerald-400' : 'text-red-400'}`}>
                Total: {total}%
              </span>
            </div>

            <div className="space-y-4">
              {Object.entries(weights).map(([key, value]) => (
                <div key={key}>
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-sm font-medium text-slate-300 capitalize">
                      {key.replace(/([A-Z])/g, ' $1')}
                    </label>
                    <input
                      type="number"
                      value={value}
                      onChange={(e) => handleWeightChange(key, Math.max(0, Math.min(100, parseInt(e.target.value) || 0)))}
                      className="w-16 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-100 text-right"
                    />
                  </div>
                  
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={value}
                    onChange={(e) => handleWeightChange(key, parseInt(e.target.value))}
                    className="w-full"
                  />
                </div>
              ))}
            </div>

            <button className="w-full mt-6 px-4 py-3 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-300 hover:bg-cyan-500/30 font-medium transition-colors">
              Apply Weights
            </button>
          </div>
        </div>

        {/* Weight Distribution */}
        <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
          <h3 className="font-semibold text-slate-100 mb-4">Distribution</h3>
          
          <div className="space-y-3">
            {Object.entries(weights).map(([key, value]) => (
              <div key={key}>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-slate-400 capitalize text-xs">{key.replace(/([A-Z])/g, ' $1')}</span>
                  <span className="text-slate-100 font-semibold">{value}%</span>
                </div>
                <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-cyan-500 to-emerald-500"
                    style={{ width: `${value}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* API Access */}
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
        <h3 className="font-semibold text-slate-100 mb-4">API Access</h3>
        
        <div className="space-y-4">
          <p className="text-slate-400">Use our API to integrate predictions into your own tools</p>
          
          <div className="p-4 rounded-lg bg-slate-800/50 border border-slate-700 font-mono text-sm">
            <p className="text-slate-300">GET /api/predictions?sport=NFL&league=Week15</p>
            <p className="text-slate-500 mt-2">X-API-Key: <span className="text-cyan-400">algobets_...</span></p>
          </div>

          <button className="px-4 py-2 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-300 hover:bg-cyan-500/30 font-medium transition-colors text-sm">
            View Full API Documentation
          </button>
        </div>
      </div>

      {/* Devig Methods */}
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
        <h3 className="font-semibold text-slate-100 mb-4">Devigging Methods</h3>
        
        <div className="space-y-3">
          {[
            { name: 'Additive (Recommended)', desc: 'Best for slight overrounds' },
            { name: 'Multiplicative', desc: 'For larger implied probability adjustments' },
            { name: 'Inverse', desc: 'Most mathematically pure' },
          ].map((method) => (
            <label key={method.name} className="flex items-start gap-3 p-4 rounded-lg bg-slate-800/50 border border-slate-700 cursor-pointer hover:border-slate-600">
              <input type="radio" name="devig" defaultChecked={method.name === 'Additive'} className="mt-1" />
              <div>
                <p className="font-medium text-slate-100">{method.name}</p>
                <p className="text-sm text-slate-400">{method.desc}</p>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Leaderboard */}
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-slate-100">Leaderboard</h3>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" defaultChecked className="w-4 h-4 rounded" />
            <span className="text-slate-300">Show my picks</span>
          </label>
        </div>

        <div className="space-y-2">
          {[
            { rank: 1, name: 'SharpPicks_', roi: '+47.2%', picks: 342 },
            { rank: 2, name: 'ProBettor_88', roi: '+43.8%', picks: 289 },
            { rank: 3, name: 'AlgoBets (You)', roi: '+18.3%', picks: 247, isYou: true },
            { rank: 4, name: 'DataDriven', roi: '+15.7%', picks: 156 },
            { rank: 5, name: 'LineWatcher', roi: '+12.4%', picks: 201 },
          ].map((user) => (
            <div
              key={user.rank}
              className={`p-3 rounded-lg flex items-center justify-between ${
                user.isYou ? 'bg-cyan-500/20 border border-cyan-500/50' : 'bg-slate-800/50 border border-slate-700'
              }`}
            >
              <div className="flex items-center gap-3">
                <span className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-sm font-bold">
                  {user.rank}
                </span>
                <span className="font-medium text-slate-100">{user.name}</span>
              </div>
              <div className="flex items-center gap-4 text-sm">
                <span className="text-slate-400">{user.picks} picks</span>
                <span className="text-emerald-400 font-semibold">{user.roi}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
