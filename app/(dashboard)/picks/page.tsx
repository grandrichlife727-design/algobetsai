'use client'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/lib/api'
import { generateMockPicks } from '@/lib/algorithms'
import { PickCard } from '@/components/pick-card'
import { formatPercent, getSportEmoji, sortByConfidence, filterByStatus } from '@/lib/utils'

export default function PicksPage() {
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedSport, setSelectedSport] = useState<string | null>(null)
  const [selectedStatus, setSelectedStatus] = useState<string | null>('pending')
  const [sortBy, setSortBy] = useState<'confidence' | 'edge' | 'kelly'>('confidence')

  // Fetch picks
  const { data: picks = [], isLoading } = useQuery({
    queryKey: ['picks', 'all'],
    queryFn: async () => {
      try {
        const result = await api.getPicks({ limit: 50 })
        return result.data || generateMockPicks(25)
      } catch {
        return generateMockPicks(25)
      }
    },
  })

  // Filter and sort picks
  const filteredPicks = picks
    .filter((p: any) => {
      if (selectedSport && p.sport !== selectedSport) return false
      if (selectedStatus && p.status !== selectedStatus) return false
      if (searchTerm) {
        const search = searchTerm.toLowerCase()
        return (
          p.event.toLowerCase().includes(search) ||
          p.pick.toLowerCase().includes(search)
        )
      }
      return true
    })
    .sort((a: any, b: any) => {
      switch (sortBy) {
        case 'edge':
          return b.edgePercent - a.edgePercent
        case 'kelly':
          return b.quarterKellyPercent - a.quarterKellyPercent
        case 'confidence':
        default:
          return b.confidence - a.confidence
      }
    })

  const stats = {
    total: picks.length,
    pending: picks.filter((p: any) => p.status === 'pending').length,
    won: picks.filter((p: any) => p.status === 'won').length,
    lost: picks.filter((p: any) => p.status === 'lost').length,
    winRate: picks.length
      ? Math.round(
          (picks.filter((p: any) => p.status === 'won').length / picks.length) * 100
        )
      : 0,
  }

  const sports = ['NFL', 'NBA', 'MLB', 'NHL', 'CFB', 'CBB']

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-foreground">Top Picks</h1>
        <p className="text-muted-foreground">Advanced AI prediction analysis with multi-factor signals</p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">Total Picks</div>
          <div className="mt-2 text-2xl font-bold text-foreground">{stats.total}</div>
        </div>
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">Pending</div>
          <div className="mt-2 text-2xl font-bold text-primary">{stats.pending}</div>
        </div>
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">Won</div>
          <div className="mt-2 text-2xl font-bold text-success">{stats.won}</div>
        </div>
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">Win Rate</div>
          <div className="mt-2 text-2xl font-bold text-foreground">{stats.winRate}%</div>
        </div>
      </div>

      {/* Filters and Controls */}
      <div className="space-y-4">
        {/* Search Bar */}
        <div className="relative">
          <input
            type="text"
            placeholder="Search picks by team or event..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full px-4 py-3 rounded-lg border border-muted bg-muted/50 text-foreground placeholder-muted-foreground focus:outline-none focus:border-primary transition-colors"
          />
          <span className="absolute right-4 top-1/2 -translate-y-1/2 text-muted-foreground">🔍</span>
        </div>

        {/* Filter Buttons */}
        <div className="flex flex-wrap gap-2">
          {/* Status Filter */}
          <div className="flex gap-2 flex-wrap">
            {['pending', 'won', 'lost'].map((status) => (
              <button
                key={status}
                onClick={() =>
                  setSelectedStatus(selectedStatus === status ? null : status)
                }
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  selectedStatus === status
                    ? status === 'won'
                      ? 'bg-success/30 text-success border border-success/50'
                      : status === 'lost'
                        ? 'bg-danger/30 text-danger border border-danger/50'
                        : 'bg-primary/30 text-primary border border-primary/50'
                    : 'bg-muted/50 text-muted-foreground border border-muted hover:border-primary'
                }`}
              >
                {status === 'pending' ? '⏳' : status === 'won' ? '✅' : '❌'} {' '}
                {status.charAt(0).toUpperCase() + status.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* Sport and Sort Controls */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {/* Sport Filter */}
          <div className="space-y-2">
            <label className="text-sm text-muted-foreground font-medium">Filter by Sport</label>
            <div className="grid grid-cols-3 gap-2">
              {sports.map((sport) => (
                <button
                  key={sport}
                  onClick={() =>
                    setSelectedSport(selectedSport === sport ? null : sport)
                  }
                  className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                    selectedSport === sport
                      ? 'bg-primary/30 text-primary border border-primary/50'
                      : 'bg-muted/50 text-muted-foreground border border-muted hover:border-primary'
                  }`}
                >
                  <span className="mr-1">{getSportEmoji(sport)}</span>
                  {sport}
                </button>
              ))}
            </div>
          </div>

          {/* Sort */}
          <div className="space-y-2">
            <label className="text-sm text-muted-foreground font-medium">Sort by</label>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as any)}
              className="w-full px-4 py-2 rounded-lg border border-muted bg-muted/50 text-foreground focus:outline-none focus:border-primary transition-colors"
            >
              <option value="confidence">Highest Confidence</option>
              <option value="edge">Highest Edge</option>
              <option value="kelly">Kelly Sizing</option>
            </select>
          </div>
        </div>
      </div>

      {/* Picks List */}
      <div className="space-y-3">
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-40 rounded-lg bg-muted/50 animate-pulse" />
            ))}
          </div>
        ) : filteredPicks.length > 0 ? (
          <>
            <div className="text-sm text-muted-foreground">
              Showing {filteredPicks.length} of {picks.length} picks
            </div>
            <div className="grid gap-3">
              {filteredPicks.map((pick: any) => (
                <PickCard key={pick.id} pick={pick} />
              ))}
            </div>
          </>
        ) : (
          <div className="rounded-lg border border-muted bg-muted/50 p-12 text-center">
            <div className="text-4xl mb-4">🔍</div>
            <p className="text-muted-foreground">No picks found matching your filters</p>
          </div>
        )}
      </div>
    </div>
  )
}
