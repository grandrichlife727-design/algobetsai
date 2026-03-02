'use client'

import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { generateMockPicks } from '@/lib/algorithms'
import { formatCurrency, formatPercent, getSportEmoji, cn } from '@/lib/utils'
import { PickCard } from '@/components/pick-card'
import { StatCard } from '@/components/stat-card'
import { TrendingBets } from '@/components/trending-bets'
import { useState } from 'react'

export default function Dashboard() {
  const [timeframe, setTimeframe] = useState<'7d' | '30d' | 'all'>('7d')

  // Fetch picks with fallback to mock data
  const { data: picks = [], isLoading } = useQuery({
    queryKey: ['picks', 'dashboard'],
    queryFn: async () => {
      try {
        const result = await api.getPicks({ limit: 15 })
        return result.data || generateMockPicks(15)
      } catch {
        // Fallback to mock data
        return generateMockPicks(15)
      }
    },
  })

  // Calculate dashboard stats
  const stats = {
    totalPicks: picks.length,
    elitePicks: picks.filter((p: any) => p.confidence >= 80).length,
    avgConfidence: picks.length
      ? Math.round(picks.reduce((sum: number, p: any) => sum + p.confidence, 0) / picks.length)
      : 0,
    projectedROI: 12.4,
  }

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold text-foreground">Dashboard</h1>
        <p className="text-muted-foreground">Track your picks and betting performance</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total Picks"
          value={stats.totalPicks}
          subtext="This period"
          icon="📊"
          trend="up"
        />
        <StatCard
          label="Elite Picks"
          value={stats.elitePicks}
          subtext="Confidence ≥ 80%"
          icon="⭐"
          trend="neutral"
        />
        <StatCard
          label="Avg Confidence"
          value={`${stats.avgConfidence}%`}
          subtext="All picks"
          icon="🎯"
          trend="neutral"
        />
        <StatCard
          label="Projected ROI"
          value={`${stats.projectedROI}%`}
          subtext="Monthly estimate"
          icon="💰"
          trend="up"
        />
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        {/* Top Picks */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-foreground">Top Picks</h2>
            <button className="text-sm text-primary hover:underline">View all →</button>
          </div>

          {isLoading ? (
            <div className="space-y-3">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="h-32 rounded-lg bg-muted/50 animate-pulse" />
              ))}
            </div>
          ) : (
            <div className="space-y-3">
              {picks
                .sort((a: any, b: any) => b.confidence - a.confidence)
                .slice(0, 5)
                .map((pick: any) => (
                  <PickCard key={pick.id} pick={pick} compact />
                ))}
            </div>
          )}
        </div>

        {/* Trending Bets Sidebar */}
        <div className="space-y-4">
          <h2 className="text-xl font-semibold text-foreground">Trending Bets</h2>
          <TrendingBets picks={picks} />
        </div>
      </div>

      {/* Performance by Sport */}
      <div className="card space-y-4">
        <h2 className="text-xl font-semibold text-foreground">Performance by Sport</h2>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {['NFL', 'NBA', 'MLB', 'NHL'].map((sport) => {
            const sportPicks = picks.filter((p: any) => p.sport === sport)
            const wins = sportPicks.filter((p: any) => p.status === 'won').length
            const winRate = sportPicks.length
              ? Math.round((wins / sportPicks.length) * 100)
              : 0
            return (
              <div
                key={sport}
                className="rounded-lg border border-muted bg-muted/30 p-4 text-center space-y-2"
              >
                <div className="text-2xl">{getSportEmoji(sport)}</div>
                <div className="font-medium text-foreground">{sport}</div>
                <div className="text-sm text-muted-foreground">
                  {sportPicks.length} picks
                </div>
                <div className={cn('text-lg font-bold', wins > 0 ? 'text-success' : 'text-muted')}>
                  {winRate}%
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
