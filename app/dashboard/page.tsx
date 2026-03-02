'use client'

import { useState, useEffect } from 'react'
import { StatCard } from '@/components/stat-card'
import { TrendingBets } from '@/components/trending-bets'
import { PickCard } from '@/components/pick-card'
import { fetchPicks } from '@/lib/api'
import type { Pick } from '@/types'

export default function Dashboard() {
  const [picks, setPicks] = useState<Pick[]>([])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    async function load() {
      try {
        const data = await fetchPicks()
        setPicks(Array.isArray(data) ? data : [])
      } catch {
        setPicks([])
      } finally {
        setIsLoading(false)
      }
    }
    load()
  }, [])

  const topPicks = picks.slice(0, 5)
  const totalPicks = picks.length
  const avgConfidence =
    picks.length > 0
      ? Math.round(picks.reduce((s, p) => s + p.confidence, 0) / picks.length)
      : 0
  const elitePicks = picks.filter((p) => p.confidence >= 75).length
  const avgEdge =
    picks.length > 0
      ? +(picks.reduce((s, p) => s + p.edgePercent, 0) / picks.length).toFixed(1)
      : 0

  return (
    <div className="p-4 md:p-8 space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl md:text-4xl font-bold text-foreground text-balance">
          Dashboard
        </h1>
        <p className="text-muted-foreground mt-1">
          Real-time sports betting intelligence powered by AI
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Total Picks" value={totalPicks} subtext="Active today" />
        <StatCard
          label="Avg Confidence"
          value={`${avgConfidence}%`}
          subtext="Weighted average"
        />
        <StatCard
          label="Elite Picks"
          value={elitePicks}
          subtext="75%+ confidence"
        />
        <StatCard
          label="Avg Edge"
          value={`+${avgEdge}%`}
          subtext="Expected value"
        />
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Top Picks */}
        <div className="lg:col-span-2 space-y-4">
          <div className="rounded-lg border border-muted bg-muted/30 p-6">
            <h2 className="text-xl font-semibold text-foreground mb-4">
              Top Picks
            </h2>

            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
              </div>
            ) : topPicks.length > 0 ? (
              <div className="space-y-3">
                {topPicks.map((pick) => (
                  <PickCard key={pick.id} pick={pick} />
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground text-center py-12">
                No picks available right now. Check back soon.
              </p>
            )}
          </div>
        </div>

        {/* Trending Bets Sidebar */}
        <div className="space-y-4">
          <div className="rounded-lg border border-muted bg-muted/30 p-6">
            <h2 className="text-xl font-semibold text-foreground mb-4">
              Trending
            </h2>
            <TrendingBets picks={picks} />
          </div>
        </div>
      </div>
    </div>
  )
}
