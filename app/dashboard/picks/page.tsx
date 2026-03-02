'use client'

import { useState } from 'react'

const mockPicks = [
  {
    id: 1,
    sport: 'NFL',
    league: 'National Football League',
    event: 'Dallas Cowboys vs Philadelphia Eagles',
    pick: 'Cowboys -3.5',
    confidence: 87,
    odds: '-110',
    kelly: '2.3%',
    signals: {
      clvEdge: 2.1,
      sharpMoney: 85,
      lineMovement: -2,
      consensus: 76,
      oddsQuality: 94,
      injuryNews: 5,
    },
    books: [
      { name: 'DraftKings', odds: '-110' },
      { name: 'FanDuel', odds: '-110' },
      { name: 'BetMGM', odds: '-115' },
      { name: 'Caesars', odds: '-105' },
    ],
    status: 'active',
  },
  {
    id: 2,
    sport: 'NBA',
    league: 'National Basketball Association',
    event: 'Los Angeles Lakers vs Boston Celtics',
    pick: 'Over 218.5 Points',
    confidence: 79,
    odds: '-110',
    kelly: '1.8%',
    signals: {
      clvEdge: 1.5,
      sharpMoney: 72,
      lineMovement: 1.5,
      consensus: 68,
      oddsQuality: 88,
      injuryNews: 15,
    },
    books: [
      { name: 'DraftKings', odds: '-110' },
      { name: 'FanDuel', odds: '-115' },
      { name: 'BetMGM', odds: '-110' },
    ],
    status: 'active',
  },
  {
    id: 3,
    sport: 'NFL',
    league: 'National Football League',
    event: 'Kansas City Chiefs vs San Francisco 49ers',
    pick: 'Chiefs -2',
    confidence: 84,
    odds: '+105',
    kelly: '2.1%',
    signals: {
      clvEdge: 1.8,
      sharpMoney: 81,
      lineMovement: -1.5,
      consensus: 79,
      oddsQuality: 91,
      injuryNews: 3,
    },
    books: [
      { name: 'Pinnacle', odds: '+105' },
      { name: 'DraftKings', odds: '+100' },
      { name: 'FanDuel', odds: '+105' },
    ],
    status: 'active',
  },
]

export default function PicksPage() {
  const [selectedPick, setSelectedPick] = useState<(typeof mockPicks)[0] | null>(null)

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 85) return 'text-emerald-400'
    if (confidence >= 75) return 'text-cyan-400'
    if (confidence >= 65) return 'text-amber-400'
    return 'text-red-400'
  }

  const getSignalColor = (value: number) => {
    if (value >= 80) return 'text-emerald-400'
    if (value >= 60) return 'text-cyan-400'
    if (value >= 40) return 'text-amber-400'
    return 'text-red-400'
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-slate-100">Top Picks</h1>
        <p className="text-slate-400 mt-1">Advanced AI predictions with multi-factor analysis</p>
      </div>

      <div className="space-y-4">
        {mockPicks.map((pick) => (
          <div
            key={pick.id}
            className="rounded-lg border border-slate-700 bg-slate-900/50 backdrop-blur-sm overflow-hidden hover:border-slate-600 transition-colors cursor-pointer"
            onClick={() => setSelectedPick(pick)}
          >
            <div className="p-6">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-3">
                    <span className="px-2 py-1 rounded bg-cyan-500/20 text-cyan-300 text-xs font-bold">
                      {pick.sport}
                    </span>
                    <span className="text-slate-400 text-sm">{pick.league}</span>
                  </div>
                  
                  <h3 className="text-lg font-semibold text-slate-100 mb-3">{pick.event}</h3>
                  
                  <div className="flex items-center gap-4 mb-4">
                    <div className="text-xl font-bold text-cyan-400">{pick.pick}</div>
                    <div className="text-slate-400">{pick.odds}</div>
                    <div className="text-emerald-400 font-medium">Kelly: {pick.kelly}</div>
                  </div>

                  <div className="flex gap-2 flex-wrap">
                    <span className="px-2 py-1 rounded text-xs bg-slate-800 text-slate-300">
                      📊 CLV +{pick.signals.clvEdge.toFixed(1)}%
                    </span>
                    <span className="px-2 py-1 rounded text-xs bg-slate-800 text-slate-300">
                      💰 Sharp: {pick.signals.sharpMoney}%
                    </span>
                    <span className="px-2 py-1 rounded text-xs bg-slate-800 text-slate-300">
                      📈 Line: {pick.signals.lineMovement > 0 ? '+' : ''}{pick.signals.lineMovement}
                    </span>
                  </div>
                </div>

                <div className="text-right">
                  <div className="flex flex-col items-center gap-2">
                    <div className={`text-4xl font-bold ${getConfidenceColor(pick.confidence)}`}>
                      {pick.confidence}%
                    </div>
                    <p className="text-xs text-slate-400">confidence</p>
                  </div>
                  <button className="mt-4 px-4 py-2 rounded bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 text-sm font-medium transition-colors">
                    View Breakdown →
                  </button>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Modal for pick details */}
      {selectedPick && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-slate-900 rounded-lg border border-slate-700 max-w-2xl w-full max-h-96 overflow-y-auto">
            <div className="p-6 border-b border-slate-700">
              <div className="flex items-center justify-between">
                <h2 className="text-2xl font-bold text-slate-100">{selectedPick.event}</h2>
                <button
                  onClick={() => setSelectedPick(null)}
                  className="text-slate-400 hover:text-slate-200 text-2xl"
                >
                  ×
                </button>
              </div>
            </div>
            
            <div className="p-6 space-y-6">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-slate-400 text-sm mb-1">Pick</p>
                  <p className="text-xl font-bold text-cyan-400">{selectedPick.pick}</p>
                </div>
                <div>
                  <p className="text-slate-400 text-sm mb-1">Confidence</p>
                  <p className={`text-xl font-bold ${getConfidenceColor(selectedPick.confidence)}`}>
                    {selectedPick.confidence}%
                  </p>
                </div>
              </div>

              <div>
                <h3 className="font-semibold text-slate-100 mb-3">Signal Breakdown</h3>
                <div className="space-y-2">
                  {Object.entries(selectedPick.signals).map(([key, value]) => (
                    <div key={key} className="flex items-center justify-between">
                      <span className="text-slate-400 text-sm capitalize">{key.replace(/([A-Z])/g, ' $1')}</span>
                      <div className="flex items-center gap-2">
                        <div className="w-32 h-2 bg-slate-700 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-cyan-500 to-emerald-500"
                            style={{ width: `${typeof value === 'number' && value > 0 ? Math.min(value, 100) : 50}%` }}
                          />
                        </div>
                        <span className={`text-sm font-medium ${getSignalColor(typeof value === 'number' ? value : 0)}`}>
                          {typeof value === 'number' ? (value > 0 ? '+' : '') + value.toFixed(1) : value}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h3 className="font-semibold text-slate-100 mb-3">Multi-Book Odds</h3>
                <div className="grid grid-cols-2 gap-2">
                  {selectedPick.books.map((book, idx) => (
                    <div key={idx} className="p-3 rounded bg-slate-800/50 border border-slate-700">
                      <p className="text-xs text-slate-400">{book.name}</p>
                      <p className="text-lg font-bold text-slate-100">{book.odds}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
