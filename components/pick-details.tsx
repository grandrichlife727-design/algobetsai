'use client'

import { Pick } from '@/types'
import { formatOdds, formatPercent, getSportEmoji, cn } from '@/lib/utils'
import { useState } from 'react'

interface PickDetailsProps {
  pick: Pick
  open: boolean
  onClose: () => void
}

export function PickDetails({ pick, open, onClose }: PickDetailsProps) {
  if (!open) return null

  const signalEntries = Object.entries(pick.signalBreakdown).map(([key, value]) => ({
    label: key.replace(/([A-Z])/g, ' $1').trim(),
    value,
    icon: {
      clvEdge: '💰',
      sharpMoney: '🏦',
      lineMovement: '📈',
      consensus: '🤝',
      oddsQuality: '⭐',
      injuryNews: '🏥',
    }[key as keyof typeof pick.signalBreakdown] || '📊',
  }))

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="bg-background border border-muted rounded-lg max-w-2xl w-full max-h-[90vh] overflow-auto">
          {/* Header */}
          <div className="sticky top-0 flex items-center justify-between p-6 border-b border-muted bg-muted/30 backdrop-blur-sm">
            <div className="flex items-center gap-3">
              <span className="text-3xl">{getSportEmoji(pick.sport)}</span>
              <div>
                <h2 className="text-xl font-bold text-foreground">{pick.pick}</h2>
                <p className="text-sm text-muted-foreground">{pick.event}</p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              ✕
            </button>
          </div>

          {/* Content */}
          <div className="p-6 space-y-6">
            {/* Quick Stats */}
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <div className="rounded-lg bg-primary/10 p-4 text-center">
                <div className="text-xs text-muted-foreground mb-1">Confidence</div>
                <div className="text-2xl font-bold text-primary">{pick.confidence}%</div>
              </div>
              <div className="rounded-lg bg-success/10 p-4 text-center">
                <div className="text-xs text-muted-foreground mb-1">Edge</div>
                <div className="text-2xl font-bold text-success">
                  {formatPercent(pick.edgePercent)}
                </div>
              </div>
              <div className="rounded-lg bg-warning/10 p-4 text-center">
                <div className="text-xs text-muted-foreground mb-1">Current Odds</div>
                <div className="text-2xl font-bold text-warning">{formatOdds(pick.currentOdds)}</div>
              </div>
              <div className="rounded-lg bg-muted/50 p-4 text-center">
                <div className="text-xs text-muted-foreground mb-1">Quarter Kelly</div>
                <div className="text-2xl font-bold text-foreground">{pick.quarterKellyPercent}%</div>
              </div>
            </div>

            {/* Signal Breakdown */}
            <div className="space-y-3">
              <h3 className="font-semibold text-foreground">Signal Breakdown</h3>
              <div className="space-y-3">
                {signalEntries.map(({ label, value, icon }) => (
                  <div key={label} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="text-sm text-muted-foreground">
                        <span className="mr-2">{icon}</span>
                        {label}
                      </label>
                      <span className="text-sm font-bold text-foreground">{value}%</span>
                    </div>
                    <div className="w-full h-2 rounded-full bg-muted overflow-hidden">
                      <div
                        className={cn(
                          'h-full rounded-full transition-all',
                          value >= 80
                            ? 'bg-success'
                            : value >= 70
                              ? 'bg-primary'
                              : value >= 60
                                ? 'bg-warning'
                                : 'bg-danger'
                        )}
                        style={{ width: `${value}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Odds Comparison */}
            <div className="space-y-3">
              <h3 className="font-semibold text-foreground">Best Odds Available</h3>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                {Object.entries(pick.odds).map(([book, odds]) => (
                  odds && (
                    <div key={book} className="rounded-lg border border-muted bg-muted/30 p-3 text-center">
                      <div className="text-xs text-muted-foreground capitalize">{book}</div>
                      <div className="text-lg font-bold text-foreground mt-1">{formatOdds(odds)}</div>
                    </div>
                  )
                ))}
              </div>
            </div>

            {/* Kelly Sizing */}
            <div className="space-y-3">
              <h3 className="font-semibold text-foreground">Kelly Criterion Sizing</h3>
              <div className="grid grid-cols-2 gap-4">
                <div className="rounded-lg bg-primary/10 p-4">
                  <div className="text-xs text-muted-foreground mb-2">Full Kelly</div>
                  <div className="text-3xl font-bold text-primary">{pick.kellyPercent}%</div>
                  <div className="text-xs text-muted-foreground mt-2">
                    {Math.round((pick.kellyPercent / 100) * 5000)} units
                  </div>
                </div>
                <div className="rounded-lg bg-success/10 p-4 border-2 border-success/50">
                  <div className="text-xs text-muted-foreground mb-2">¼ Kelly (Recommended)</div>
                  <div className="text-3xl font-bold text-success">{pick.quarterKellyPercent}%</div>
                  <div className="text-xs text-muted-foreground mt-2">
                    {pick.recommendedUnits} units
                  </div>
                </div>
              </div>
            </div>

            {/* Notes */}
            {pick.notes && (
              <div className="rounded-lg bg-muted/50 p-4">
                <p className="text-sm text-muted-foreground">
                  <span className="font-semibold text-foreground">Notes: </span>
                  {pick.notes}
                </p>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="sticky bottom-0 flex gap-3 p-6 border-t border-muted bg-muted/30 backdrop-blur-sm">
            <button
              onClick={onClose}
              className="flex-1 rounded-lg border border-muted py-2 px-4 text-sm text-foreground hover:bg-muted/50 transition-colors"
            >
              Close
            </button>
            <button className="flex-1 rounded-lg bg-primary py-2 px-4 text-sm text-primary-foreground hover:bg-primary/90 transition-colors font-medium">
              Place Bet 🎲
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
