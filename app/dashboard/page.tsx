'use client'

import { useState, useEffect } from 'react'

interface Stat {
  label: string
  value: string
  change: number
  icon: string
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stat[]>([])
  const [picks, setPicks] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Mock data for demo
    const mockStats: Stat[] = [
      { label: 'Total Picks', value: '247', change: 12.5, icon: '🎯' },
      { label: 'Win Rate', value: '62.4%', change: 3.2, icon: '✅' },
      { label: 'Avg Confidence', value: '76.8%', change: -1.5, icon: '📊' },
      { label: 'Projected ROI', value: '+18.3%', change: 5.7, icon: '💰' },
    ]

    const mockPicks = [
      {
        id: 1,
        sport: 'NFL',
        event: 'Cowboys vs Eagles',
        pick: 'Cowboys -3.5',
        confidence: 87,
        odds: '-110',
        status: 'active',
        signals: ['CLV Edge', 'Sharp Money', 'Line Movement'],
      },
      {
        id: 2,
        sport: 'NBA',
        event: 'Lakers vs Celtics',
        pick: 'Over 218.5',
        confidence: 79,
        odds: '-110',
        status: 'active',
        signals: ['Consensus', 'Injury Impact'],
      },
      {
        id: 3,
        sport: 'NFL',
        event: 'Chiefs vs 49ers',
        pick: 'Chiefs -2',
        confidence: 84,
        odds: '+105',
        status: 'active',
        signals: ['Sharp Money', 'Line Movement', 'Consensus'],
      },
      {
        id: 4,
        sport: 'MLB',
        event: 'Yankees vs Red Sox',
        pick: 'Yankees -120',
        confidence: 71,
        odds: '-110',
        status: 'active',
        signals: ['CLV Edge', 'Odds Quality'],
      },
      {
        id: 5,
        sport: 'NBA',
        event: 'Suns vs Nuggets',
        pick: 'Suns +4.5',
        confidence: 82,
        odds: '-110',
        status: 'active',
        signals: ['Sharp Money', 'Consensus', 'CLV Edge'],
      },
    ]

    setStats(mockStats)
    setPicks(mockPicks)
    setLoading(false)
  }, [])

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 85) return 'text-emerald-400'
    if (confidence >= 75) return 'text-cyan-400'
    if (confidence >= 65) return 'text-amber-400'
    return 'text-red-400'
  }

  const getConfidenceBg = (confidence: number) => {
    if (confidence >= 85) return 'bg-emerald-500/20'
    if (confidence >= 75) return 'bg-cyan-500/20'
    if (confidence >= 65) return 'bg-amber-500/20'
    return 'bg-red-500/20'
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-cyan-500 mx-auto"></div>
          <p className="mt-4 text-slate-400">Loading dashboard...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {stats.map((stat, idx) => (
          <div
            key={idx}
            className="rounded-lg border border-slate-700 bg-slate-900/50 backdrop-blur-sm p-6 hover:border-slate-600 transition-colors"
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-slate-400 text-sm font-medium">{stat.label}</p>
                <p className="text-3xl font-bold text-slate-100 mt-2">{stat.value}</p>
              </div>
              <span className="text-3xl">{stat.icon}</span>
            </div>
            <div className="mt-4">
              <span className={`text-sm font-medium ${stat.change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {stat.change >= 0 ? '+' : ''}{stat.change}% this week
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Top Picks Section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-xl font-bold text-slate-100">Top Picks (Trending)</h3>
          <a href="/dashboard/picks" className="text-cyan-400 hover:text-cyan-300 text-sm font-medium">
            View All →
          </a>
        </div>

        <div className="space-y-3">
          {picks.map((pick) => (
            <div
              key={pick.id}
              className="rounded-lg border border-slate-700 bg-slate-900/50 backdrop-blur-sm p-4 hover:border-slate-600 transition-colors hover:bg-slate-900/70 cursor-pointer"
            >
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3">
                    <span className="px-2 py-1 rounded bg-slate-800 text-slate-200 text-xs font-medium">
                      {pick.sport}
                    </span>
                    <span className="text-slate-100 font-semibold">{pick.event}</span>
                  </div>
                  <div className="mt-2 flex items-center gap-4">
                    <span className="text-slate-300">{pick.pick}</span>
                    <span className="text-slate-500 text-sm">{pick.odds}</span>
                    <div className="flex gap-1 flex-wrap">
                      {pick.signals.slice(0, 2).map((signal: string, idx: number) => (
                        <span key={idx} className="px-2 py-1 rounded text-xs bg-slate-800 text-slate-300">
                          {signal}
                        </span>
                      ))}
                      {pick.signals.length > 2 && (
                        <span className="px-2 py-1 rounded text-xs bg-slate-800 text-slate-400">
                          +{pick.signals.length - 2}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <div className={`text-2xl font-bold ${getConfidenceColor(pick.confidence)}`}>
                      {pick.confidence}%
                    </div>
                    <p className="text-xs text-slate-400">confidence</p>
                  </div>
                  <div className={`w-16 h-16 rounded-full flex items-center justify-center border-2 ${getConfidenceBg(pick.confidence)} border-slate-700`}>
                    <div className="text-center">
                      <div className={`text-lg font-bold ${getConfidenceColor(pick.confidence)}`}>
                        {pick.confidence}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <a
          href="/dashboard/parlay"
          className="rounded-lg border border-slate-700 bg-gradient-to-br from-purple-900/30 to-slate-900/50 p-6 hover:border-purple-500/50 transition-colors"
        >
          <h4 className="font-semibold text-slate-100 mb-2">Build Parlay</h4>
          <p className="text-sm text-slate-400">Create optimized parlays with Kelly sizing</p>
        </a>
        
        <a
          href="/dashboard/analytics"
          className="rounded-lg border border-slate-700 bg-gradient-to-br from-emerald-900/30 to-slate-900/50 p-6 hover:border-emerald-500/50 transition-colors"
        >
          <h4 className="font-semibold text-slate-100 mb-2">View Analytics</h4>
          <p className="text-sm text-slate-400">Track performance, ROI, and Sharpe ratio</p>
        </a>
        
        <a
          href="/dashboard/sharp"
          className="rounded-lg border border-slate-700 bg-gradient-to-br from-cyan-900/30 to-slate-900/50 p-6 hover:border-cyan-500/50 transition-colors"
        >
          <h4 className="font-semibold text-slate-100 mb-2">Sharp Tools</h4>
          <p className="text-sm text-slate-400">Advanced customization and API access</p>
        </a>
      </div>
    </div>
  )
}
