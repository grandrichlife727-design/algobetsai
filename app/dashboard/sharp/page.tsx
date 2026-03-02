'use client'

import { useState } from 'react'

export default function SharpToolsPage() {
  const [weights, setWeights] = useState({
    clv: 25,
    sharpMoney: 20,
    lineMovement: 20,
    consensus: 15,
    oddsQuality: 10,
    news: 10
  })

  const handleWeightChange = (key: string, value: number) => {
    const total = Object.values({ ...weights, [key]: value }).reduce((a, b) => a + b, 0)
    if (total === 100) {
      setWeights({ ...weights, [key]: value })
    }
  }

  const totalWeight = Object.values(weights).reduce((a, b) => a + b, 0)

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white mb-2">Sharp Tools</h1>
        <p className="text-slate-400">Advanced customization for professional bettors</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Model Weights */}
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-6">
          <h2 className="text-xl font-semibold text-white mb-4">Prediction Model Weights</h2>
          <p className="text-sm text-slate-400 mb-6">Customize how each signal impacts confidence</p>

          <div className="space-y-5">
            {Object.entries(weights).map(([key, value]) => {
              const labels: Record<string, string> = {
                clv: 'CLV Edge',
                sharpMoney: 'Sharp Money',
                lineMovement: 'Line Movement',
                consensus: 'Expert Consensus',
                oddsQuality: 'Odds Quality',
                news: 'Injury/News'
              }

              return (
                <div key={key}>
                  <div className="flex justify-between items-center mb-2">
                    <label className="text-white font-medium">{labels[key]}</label>
                    <input
                      type="number"
                      min="0"
                      max="100"
                      value={value}
                      onChange={(e) => handleWeightChange(key, Number(e.target.value))}
                      className="w-16 px-2 py-1 rounded bg-slate-800 border border-slate-700 text-white text-center"
                    />
                    <span className="text-slate-400">%</span>
                  </div>
                  <div className="w-full bg-slate-800 rounded-full h-2">
                    <div 
                      className="bg-cyan-500 h-2 rounded-full transition-all"
                      style={{ width: `${value}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>

          <div className="mt-6 p-3 bg-slate-800 rounded">
            <p className="text-sm text-slate-400">Total Weight</p>
            <p className={`text-lg font-bold ${totalWeight === 100 ? 'text-emerald-400' : 'text-red-400'}`}>
              {totalWeight}%
            </p>
          </div>

          <button className="w-full mt-4 px-4 py-2 bg-cyan-600 hover:bg-cyan-700 text-white rounded font-medium">
            Save Preset
          </button>
        </div>

        {/* API Access */}
        <div className="space-y-4">
          <div className="bg-slate-900 rounded-lg border border-slate-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">API Documentation</h2>
            
            <div className="space-y-4">
              <div>
                <h3 className="text-sm font-semibold text-white mb-2">Endpoint</h3>
                <code className="block bg-slate-800 p-3 rounded text-sm text-cyan-400 overflow-x-auto">
                  GET /api/v1/picks
                </code>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-white mb-2">Authentication</h3>
                <code className="block bg-slate-800 p-3 rounded text-sm text-slate-300 overflow-x-auto">
                  Authorization: Bearer YOUR_API_KEY
                </code>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-white mb-2">Response Format</h3>
                <code className="block bg-slate-800 p-3 rounded text-sm text-slate-300 text-xs overflow-x-auto font-mono">
{`{
  "picks": [{
    "id": "pick_123",
    "team": "Lakers",
    "pick": "ML",
    "confidence": 78.5,
    "signals": {...},
    "kelly_sizing": {...}
  }]
}`}
                </code>
              </div>

              <button className="w-full px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white rounded font-medium">
                View Full API Docs
              </button>
            </div>
          </div>

          {/* Leaderboard */}
          <div className="bg-slate-900 rounded-lg border border-slate-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Community Leaderboard</h2>
            <p className="text-sm text-slate-400 mb-4">Top performing models and custom weights</p>
            
            <div className="space-y-3">
              <div className="flex items-center justify-between p-3 bg-slate-800 rounded">
                <div>
                  <p className="font-medium text-white">Sharp Model v3</p>
                  <p className="text-xs text-slate-500">ROI: +32.4%</p>
                </div>
                <button className="text-xs px-2 py-1 bg-cyan-600 hover:bg-cyan-700 text-white rounded">
                  Use
                </button>
              </div>
              <div className="flex items-center justify-between p-3 bg-slate-800 rounded">
                <div>
                  <p className="font-medium text-white">Conservative Approach</p>
                  <p className="text-xs text-slate-500">ROI: +18.9%</p>
                </div>
                <button className="text-xs px-2 py-1 bg-cyan-600 hover:bg-cyan-700 text-white rounded">
                  Use
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
