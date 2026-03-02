'use client'

import { Pick } from '@/types'
import { formatOdds, formatPercent, getConfidenceColor, getSportEmoji, cn } from '@/lib/utils'
import { useState } from 'react'
import { PickDetails } from './pick-details'

interface PickCardProps {
  pick: Pick
  compact?: boolean
}

export function PickCard({ pick, compact = false }: PickCardProps) {
  const [showDetails, setShowDetails] = useState(false)

  const edgeColor = pick.edgePercent > 2 ? 'text-success' : pick.edgePercent > 0 ? 'text-primary' : 'text-danger'

  return (
    <>
      <div
        className={cn(
          'rounded-lg border border-muted bg-muted/50 p-4 hover:border-primary/50 transition-all cursor-pointer',
          'group'
        )}
        onClick={() => setShowDetails(true)}
      >
        <div className="flex items-start justify-between gap-4">
          {/* Left Section */}
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-2xl">{getSportEmoji(pick.sport)}</span>
              <div>
                <p className="font-bold text-foreground">{pick.pick}</p>
                <p className="text-sm text-muted-foreground">{pick.event}</p>
              </div>
            </div>
          </div>

          {/* Right Section - Confidence Gauge */}
          <div className="flex flex-col items-end gap-3">
            {/* Circular Confidence */}
            <div className="relative w-20 h-20">
              <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
                {/* Background circle */}
                <circle
                  cx="50"
                  cy="50"
                  r="45"
                  fill="none"
                  stroke="hsl(var(--muted))"
                  strokeWidth="8"
                />
                {/* Progress circle */}
                <circle
                  cx="50"
                  cy="50"
                  r="45"
                  fill="none"
                  stroke={
                    pick.confidence >= 80
                      ? 'hsl(var(--success))'
                      : pick.confidence >= 70
                        ? 'hsl(var(--primary))'
                        : pick.confidence >= 60
                          ? 'hsl(var(--warning))'
                          : 'hsl(var(--danger))'
                  }
                  strokeWidth="8"
                  strokeDasharray={`${2.82 * pick.confidence} 282`}
                  strokeLinecap="round"
                  className="transition-all duration-500"
                />
              </svg>
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="text-center">
                  <div className={cn('text-2xl font-bold', getConfidenceColor(pick.confidence))}>
                    {pick.confidence}%
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {!compact && (
          <>
            {/* Signal Tags */}
            <div className="mt-4 flex flex-wrap gap-2">
              {pick.signalBreakdown.clvEdge > 70 && (
                <span className="badge-success">Strong CLV Edge</span>
              )}
              {pick.signalBreakdown.sharpMoney > 75 && (
                <span className="badge-success">Sharp Agreement</span>
              )}
              {pick.signalBreakdown.lineMovement > 70 && (
                <span className="badge-warning">Significant Movement</span>
              )}
              {pick.signalBreakdown.consensus > 80 && (
                <span className="badge-success">Strong Consensus</span>
              )}
            </div>

            {/* Stats Row */}
            <div className="mt-4 grid grid-cols-4 gap-2">
              <div className="rounded bg-primary/10 p-2 text-center">
                <div className="text-xs text-muted-foreground">Odds</div>
                <div className="font-mono text-sm font-bold text-foreground">
                  {formatOdds(pick.currentOdds)}
                </div>
              </div>
              <div className="rounded bg-muted/50 p-2 text-center">
                <div className="text-xs text-muted-foreground">Implied</div>
                <div className="font-mono text-sm font-bold text-foreground">
                  {formatPercent(pick.impliedProb)}
                </div>
              </div>
              <div className={cn('rounded p-2 text-center', edgeColor === 'text-success' ? 'bg-success/10' : 'bg-muted/50')}>
                <div className="text-xs text-muted-foreground">Edge</div>
                <div className={cn('font-mono text-sm font-bold', edgeColor)}>
                  {formatPercent(pick.edgePercent)}
                </div>
              </div>
              <div className="rounded bg-primary/10 p-2 text-center">
                <div className="text-xs text-muted-foreground">Kelly</div>
                <div className="font-mono text-sm font-bold text-primary">
                  {pick.quarterKellyPercent}%
                </div>
              </div>
            </div>
          </>
        )}

        <button
          onClick={(e) => {
            e.stopPropagation()
            setShowDetails(true)
          }}
          className="mt-4 w-full rounded border border-primary/50 py-2 px-3 text-sm text-primary hover:bg-primary/10 transition-colors"
        >
          View Details 📊
        </button>
      </div>

      {/* Details Modal */}
      <PickDetails
        pick={pick}
        open={showDetails}
        onClose={() => setShowDetails(false)}
      />
    </>
  )
}
