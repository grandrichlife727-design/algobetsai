'use client'

import { useState, useEffect } from 'react'
import { StatCard } from '@/components/stat-card'
import { TrendingBets } from '@/components/trending-bets'
import { PickCard } from '@/components/pick-card'
import { PickDetails } from '@/components/pick-details'
import { fetchPicks } from '@/lib/api'
import type { Pick } from '@/types'

export default function Dashboard() {
  const [picks, setPicks] = useState<Pick[]>([])
  const [selectedPick, setSelectedPick] = useState<Pick | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    loadPicks()
  }, [])

  const loadPicks = async () => {
    try {
      const data = await fetchPicks()
      setPicks(data.slice(0, 5))
    } catch (error) {
      console.error('Failed to load picks:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const totalPicks = picks.length
  const avgConfidence = picks.length > 0 
    ? Math.round(picks.reduce((sum, p) => sum + p.confidence, 0) / picks.length)
    : 0
  const elitePicks = picks.filter(p => p.confidence >= 75).length
  const projectedRoi = Math.round((totalPicks * 1.05) - totalPicks) // 5% avg ROI per pick

  const handlePickClick = (pick: Pick) => {
    setSelectedPick(pick)
    setIsModalOpen(true)
  }

  return (
    <div className="p-8 space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-4xl font-bold text-white mb-2">Dashboard</h1>
        <p className="text-slate-400">Real-time sports betting intelligence powered by AI</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard 
          label="Total Picks" 
          value={totalPicks}
          subtext="This period"
        />
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
          label="Projected ROI" 
          value={`+${projectedRoi}%`}
          subtext="Based on current picks"
        />
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Top Picks */}
        <div className="lg:col-span-2">
          <div className="bg-slate-900 rounded-lg border border-slate-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Top Picks</h2>
            {isLoading ? (
              <div className="text-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-500 mx-auto"></div>
              </div>
            ) : picks.length > 0 ? (
              <div className="space-y-3">
                {picks.map((pick) => (
                  <div
                    key={pick.id}
                    onClick={() => handlePickClick(pick)}
                    className="cursor-pointer"
                  >
                    <PickCard pick={pick} />
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-slate-400 text-center py-8">No picks available</p>
            )}
          </div>
        </div>

        {/* Trending Bets */}
        <div>
          <TrendingBets />
        </div>
      </div>

      {/* Pick Details Modal */}
      {selectedPick && (
        <PickDetails 
          pick={selectedPick} 
          isOpen={isModalOpen}
          onClose={() => setIsModalOpen(false)}
        />
      )}
    </div>
  )
}
