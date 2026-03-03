'use client'

import { Pick } from '@/types'
import { formatPercent, getSportEmoji, cn } from '@/lib/utils'

interface TrendingBetsProps {
  picks: Pick[]
}

export function TrendingBets({ picks }: TrendingBetsProps) {
  const trending = picks
    .filter((p) => p.confidence >= 75)
    .sort((a, b) => b.edgePercent - a.edgePercent)
    .slice(0, 5)

  if (trending.length === 0) {
    return (
      <div className="rounded-lg border border-muted bg-muted/50 p-6 text-center">
        <p className="text-muted-foreground">No high-confidence picks yet</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {trending.map((pick) => (
        <div
          key={pick.id}
          className="rounded-lg border border-muted bg-muted/50 p-3 hover:border-primary/50 transition-all cursor-pointer"
        >
          <div className="flex items-center gap-2 mb-2">
            <span className="text-lg">{getSportEmoji(pick.sport)}</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground truncate">{pick.pick}</p>
              <p className="text-xs text-muted-foreground truncate">{pick.event}</p>
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs bg-primary/20 text-primary px-2 py-1 rounded">
              {pick.confidence}%
            </div>
            <div className={cn(
              'text-xs font-bold px-2 py-1 rounded',
              pick.edgePercent > 2 ? 'bg-success/20 text-success' : 'bg-muted/50 text-muted-foreground'
            )}>
              +{formatPercent(pick.edgePercent)}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
