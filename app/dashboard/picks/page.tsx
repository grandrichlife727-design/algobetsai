'use client'

import { useState, useEffect } from 'react'
import { PickCard } from '@/components/pick-card'
import { PickDetails } from '@/components/pick-details'
import { fetchPicks } from '@/lib/api'
import type { Pick } from '@/types'

export default function PicksPage() {
  const [picks, setPicks] = useState<Pick[]>([])
  const [filteredPicks, setFilteredPicks] = useState<Pick[]>([])
  const [selectedPick, setSelectedPick] = useState<Pick | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const [sportFilter, setSportFilter] = useState<string>('all')
  const [confidenceFilter, setConfidenceFilter] = useState<number>(0)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    loadPicks()
  }, [])

  useEffect(() => {
    filterPicks()
  }, [picks, searchTerm, sportFilter, confidenceFilter])

  const loadPicks = async () => {
    try {
      const data = await fetchPicks()
      setPicks(data)
    } catch (error) {
      console.error('Failed to load picks:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const filterPicks = () => {
    let filtered = picks

    if (searchTerm) {
      filtered = filtered.filter(p =>
        p.team.toLowerCase().includes(searchTerm.toLowerCase()) ||
        p.opponent.toLowerCase().includes(searchTerm.toLowerCase())
      )
    }

    if (sportFilter !== 'all') {
      filtered = filtered.filter(p => p.sport === sportFilter)
    }

    if (confidenceFilter > 0) {
      filtered = filtered.filter(p => p.confidence >= confidenceFilter)
    }

    setFilteredPicks(filtered)
  }

  const sports = ['all', ...new Set(picks.map(p => p.sport))]

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white mb-2">Top Picks</h1>
        <p className="text-slate-400">AI-generated predictions with confidence breakdowns</p>
      </div>

      {/* Filters */}
      <div className="bg-slate-900 rounded-lg border border-slate-800 p-6 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <input
            type="text"
            placeholder="Search team or opponent..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="px-4 py-2 rounded bg-slate-800 border border-slate-700 text-white placeholder-slate-500"
          />
          <select
            value={sportFilter}
            onChange={(e) => setSportFilter(e.target.value)}
            className="px-4 py-2 rounded bg-slate-800 border border-slate-700 text-white"
          >
            {sports.map(sport => (
              <option key={sport} value={sport}>
                {sport === 'all' ? 'All Sports' : sport.toUpperCase()}
              </option>
            ))}
          </select>
          <select
            value={confidenceFilter}
            onChange={(e) => setConfidenceFilter(Number(e.target.value))}
            className="px-4 py-2 rounded bg-slate-800 border border-slate-700 text-white"
          >
            <option value={0}>All Confidence Levels</option>
            <option value={60}>60%+ Confidence</option>
            <option value={70}>70%+ Confidence</option>
            <option value={80}>80%+ Confidence</option>
            <option value={90}>90%+ Confidence</option>
          </select>
        </div>
      </div>

      {/* Results */}
      <div className="space-y-3">
        {isLoading ? (
          <div className="text-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-500 mx-auto"></div>
          </div>
        ) : filteredPicks.length > 0 ? (
          filteredPicks.map(pick => (
            <div
              key={pick.id}
              onClick={() => {
                setSelectedPick(pick)
                setIsModalOpen(true)
              }}
              className="cursor-pointer"
            >
              <PickCard pick={pick} />
            </div>
          ))
        ) : (
          <div className="text-center py-12 bg-slate-900 rounded-lg border border-slate-800">
            <p className="text-slate-400">No picks match your filters</p>
          </div>
        )}
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
