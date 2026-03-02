'use client'

import { useState } from 'react'

export default function SharpToolsPage() {
  const [modelWeights, setModelWeights] = useState({
    clvEdge: 25,
    sharpMoney: 20,
    lineMovement: 20,
    consensus: 15,
    oddsQuality: 10,
    injuryNews: 10,
  })

  const [derigMethod, setDerigMethod] = useState('standard')

  const totalWeight = Object.values(modelWeights).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-foreground">Sharp Tools</h1>
        <p className="text-muted-foreground">
          Advanced configuration and model customization for professional bettors
        </p>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        {/* Model Configuration */}
        <div className="lg:col-span-2 space-y-6">
          {/* Signal Weights */}
          <div className="rounded-lg border border-muted bg-muted/50 p-6 space-y-6">
            <div>
              <h3 className="font-semibold text-foreground mb-1">Model Signal Weights</h3>
              <p className="text-sm text-muted-foreground">
                Customize the influence of each signal on predictions
              </p>
            </div>

            <div className="space-y-4">
              {Object.entries(modelWeights).map(([signal, weight]) => (
                <div key={signal} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-foreground capitalize">
                      {signal.replace(/([A-Z])/g, ' $1').trim()}
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={weight}
                        onChange={(e) =>
                          setModelWeights({
                            ...modelWeights,
                            [signal]: parseInt(e.target.value),
                          })
                        }
                        className="w-32"
                      />
                      <span className="font-bold text-primary w-8 text-right">{weight}%</span>
                    </div>
                  </div>
                  <div className="w-full h-2 rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full rounded-full bg-primary transition-all"
                      style={{ width: `${(weight / 100) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>

            <div className="border-t border-muted pt-4">
              <div className="flex items-center justify-between p-3 rounded-lg bg-primary/10">
                <span className="text-sm font-medium text-primary">Total Weight</span>
                <span className={`font-bold ${totalWeight === 100 ? 'text-success' : 'text-warning'}`}>
                  {totalWeight}%
                </span>
              </div>
              {totalWeight !== 100 && (
                <p className="text-xs text-warning mt-2">Weights must sum to 100%</p>
              )}
            </div>

            <div className="flex gap-3">
              <button className="flex-1 btn-secondary">Reset to Default</button>
              <button className="flex-1 btn-primary" disabled={totalWeight !== 100}>
                Save Weights
              </button>
            </div>
          </div>

          {/* Devig Methods */}
          <div className="rounded-lg border border-muted bg-muted/50 p-6 space-y-6">
            <div>
              <h3 className="font-semibold text-foreground mb-1">Implied Probability Method</h3>
              <p className="text-sm text-muted-foreground">
                Choose how to calculate fair odds from sharp consensus
              </p>
            </div>

            <div className="space-y-3">
              {[
                {
                  id: 'standard',
                  name: 'Standard Vig',
                  desc: 'Even vig removal across all outcomes',
                },
                {
                  id: 'power',
                  name: 'Power Vig',
                  desc: 'Proportional vig based on closing odds',
                },
                {
                  id: 'wpo',
                  name: 'Weighted Power Order',
                  desc: 'Advanced algorithm combining multiple methods',
                },
                {
                  id: 'br',
                  name: 'Bettor Remaining',
                  desc: 'Based on relative betting volume',
                },
              ].map((method) => (
                <button
                  key={method.id}
                  onClick={() => setDerigMethod(method.id)}
                  className={`w-full text-left p-4 rounded-lg border transition-all ${
                    derigMethod === method.id
                      ? 'border-primary bg-primary/10'
                      : 'border-muted bg-muted/50 hover:border-primary/50'
                  }`}
                >
                  <div className="font-medium text-foreground">{method.name}</div>
                  <div className="text-sm text-muted-foreground">{method.desc}</div>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          {/* API Documentation */}
          <div className="rounded-lg border border-muted bg-muted/50 p-6 space-y-4">
            <h3 className="font-semibold text-foreground">API Documentation</h3>
            <p className="text-sm text-muted-foreground">
              Integration endpoints for automated betting systems
            </p>

            <div className="space-y-2 text-xs font-mono text-primary bg-muted/50 p-3 rounded border border-muted overflow-auto max-h-48">
              <div>GET /api/picks</div>
              <div>GET /api/predictions</div>
              <div>POST /api/bets</div>
              <div>GET /api/performance</div>
              <div>POST /api/parlay</div>
            </div>

            <button className="w-full btn-secondary text-sm">View Full Docs 📖</button>
          </div>

          {/* Model Metrics */}
          <div className="rounded-lg border border-muted bg-muted/50 p-6 space-y-4">
            <h3 className="font-semibold text-foreground">Model Metrics</h3>

            <div className="space-y-3 text-sm">
              <div>
                <div className="text-muted-foreground">Accuracy</div>
                <div className="font-bold text-foreground">58.3%</div>
              </div>
              <div>
                <div className="text-muted-foreground">Precision</div>
                <div className="font-bold text-foreground">62.1%</div>
              </div>
              <div>
                <div className="text-muted-foreground">Recall</div>
                <div className="font-bold text-foreground">55.8%</div>
              </div>
              <div>
                <div className="text-muted-foreground">F1 Score</div>
                <div className="font-bold text-foreground">0.589</div>
              </div>
            </div>
          </div>

          {/* Leaderboard */}
          <div className="rounded-lg border border-muted bg-muted/50 p-6 space-y-4">
            <h3 className="font-semibold text-foreground">User Leaderboard</h3>
            <p className="text-xs text-muted-foreground">
              Opt-in to display your stats publicly
            </p>

            <div className="space-y-2">
              <div className="flex items-center justify-between p-2 rounded bg-muted/50">
                <span className="text-sm font-medium text-foreground">🥇 Pro Sharp</span>
                <span className="text-xs text-success">67.2% WR</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-muted/50">
                <span className="text-sm font-medium text-foreground">🥈 Analytics 23</span>
                <span className="text-xs text-success">64.1% WR</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-muted/50">
                <span className="text-sm font-medium text-foreground">🥉 Algo Master</span>
                <span className="text-xs text-success">61.8% WR</span>
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm mt-4">
              <input type="checkbox" className="rounded" />
              <span className="text-muted-foreground">Show my stats on leaderboard</span>
            </label>
          </div>
        </div>
      </div>
    </div>
  )
}
