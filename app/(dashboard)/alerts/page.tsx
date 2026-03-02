'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/lib/api'
import { formatTime, getSportEmoji, cn } from '@/lib/utils'

export default function AlertsPage() {
  const [filter, setFilter] = useState<'all' | 'unread' | 'steam' | 'rlm'>('all')
  const queryClient = useQueryClient()

  // Fetch alerts
  const { data: alerts = [], isLoading } = useQuery({
    queryKey: ['alerts', 'all'],
    queryFn: async () => {
      try {
        const result = await api.getAlerts(100)
        return result.data || generateMockAlerts(20)
      } catch {
        return generateMockAlerts(20)
      }
    },
  })

  // Mark as read mutation
  const { mutate: markAsRead } = useMutation({
    mutationFn: async (id: string) => {
      return api.markAlertAsRead(id)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
  })

  const filteredAlerts = alerts.filter((a: any) => {
    if (filter === 'unread') return !a.read
    if (filter !== 'all') return a.type === filter
    return true
  })

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-foreground">Real-Time Alerts</h1>
        <p className="text-muted-foreground">Steam moves, RLM, line changes, and odds boosts</p>
      </div>

      {/* Alert Filters */}
      <div className="flex gap-2 flex-wrap">
        {['all', 'unread', 'steam', 'rlm'].map((type) => (
          <button
            key={type}
            onClick={() => setFilter(type as any)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              filter === type
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:bg-muted/80'
            }`}
          >
            {type === 'all'
              ? '📋 All Alerts'
              : type === 'unread'
                ? '🔔 Unread'
                : type === 'steam'
                  ? '💨 Steam Moves'
                  : '📉 Reverse Line'}
          </button>
        ))}
      </div>

      {/* Alerts List */}
      <div className="space-y-3">
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-24 rounded-lg bg-muted/50 animate-pulse" />
            ))}
          </div>
        ) : filteredAlerts.length > 0 ? (
          filteredAlerts.map((alert: any) => (
            <div
              key={alert.id}
              className={cn(
                'rounded-lg border p-4 cursor-pointer transition-all hover:border-primary/50',
                alert.read ? 'border-muted bg-muted/30' : 'border-primary/50 bg-primary/10'
              )}
              onClick={() => !alert.read && markAsRead(alert.id)}
            >
              <div className="flex items-start gap-4">
                {/* Alert Icon */}
                <div className="text-3xl flex-shrink-0">
                  {alert.type === 'steam'
                    ? '💨'
                    : alert.type === 'rlm'
                      ? '📉'
                      : alert.type === 'odds_boost'
                        ? '⚡'
                        : alert.type === 'line_move'
                          ? '📈'
                          : '🔔'}
                </div>

                {/* Alert Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div>
                      <h3 className="font-semibold text-foreground">{alert.title}</h3>
                      <p className="text-sm text-muted-foreground">{alert.event}</p>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className="text-sm font-mono text-primary font-bold">
                        {alert.percentChange > 0 ? '+' : ''}
                        {alert.percentChange}%
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {formatTime(alert.timestamp)}
                      </div>
                    </div>
                  </div>
                  <p className="text-sm text-muted-foreground">{alert.description}</p>

                  {/* Alert Metadata */}
                  <div className="mt-2 flex gap-2 text-xs">
                    <span className="bg-primary/20 text-primary px-2 py-1 rounded">
                      {getSportEmoji(alert.sport)} {alert.sport}
                    </span>
                    <span className="bg-muted px-2 py-1 rounded text-muted-foreground capitalize">
                      {alert.type.replace('_', ' ')}
                    </span>
                  </div>
                </div>

                {/* Unread Indicator */}
                {!alert.read && (
                  <div className="w-3 h-3 rounded-full bg-primary flex-shrink-0 mt-1" />
                )}
              </div>
            </div>
          ))
        ) : (
          <div className="rounded-lg border border-muted bg-muted/50 p-12 text-center">
            <div className="text-4xl mb-4">✨</div>
            <p className="text-muted-foreground">No alerts at this time</p>
          </div>
        )}
      </div>
    </div>
  )
}

// Mock alerts generator
function generateMockAlerts(count: number) {
  const types = ['steam', 'rlm', 'odds_boost', 'line_move', 'sharp_move'] as const
  const sports = ['NFL', 'NBA', 'MLB', 'NHL']
  const events = [
    'Chiefs vs Eagles',
    'Celtics vs Nuggets',
    'Yankees vs Dodgers',
    'Hurricanes vs Maple Leafs',
  ]

  return Array.from({ length: count }, (_, i) => ({
    id: `alert-${i}`,
    type: types[Math.floor(Math.random() * types.length)],
    title:
      types[Math.floor(Math.random() * types.length)] === 'steam'
        ? 'Steam Detected'
        : 'Line Movement Alert',
    description: 'Sharp money detected on this pick, moving with the consensus',
    sport: sports[Math.floor(Math.random() * sports.length)],
    event: events[Math.floor(Math.random() * events.length)],
    percentChange: Math.random() * 20 - 10,
    timestamp: Date.now() - Math.random() * 3600000,
    read: Math.random() > 0.3,
  }))
}
