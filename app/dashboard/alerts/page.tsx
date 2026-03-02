'use client'

import { useState, useEffect } from 'react'

interface Alert {
  id: string
  type: 'steam' | 'rlm' | 'odds_boost' | 'line_move'
  title: string
  description: string
  timestamp: Date
  severity: 'low' | 'medium' | 'high'
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([])

  useEffect(() => {
    // Generate mock alerts
    const mockAlerts: Alert[] = [
      {
        id: '1',
        type: 'steam',
        title: 'Strong Steam on Lakers ML',
        description: 'Professional money detected: -2 → -1.5 in 15 minutes',
        timestamp: new Date(Date.now() - 5 * 60000),
        severity: 'high'
      },
      {
        id: '2',
        type: 'rlm',
        title: 'Reverse Line Move on Celtics -7',
        description: 'Sharp contrarian action: Line moved -1 against heavy public',
        timestamp: new Date(Date.now() - 12 * 60000),
        severity: 'high'
      },
      {
        id: '3',
        type: 'odds_boost',
        title: 'Enhanced Odds: Cowboys Moneyline',
        description: 'DraftKings boosting CWB ML from -110 to +120',
        timestamp: new Date(Date.now() - 20 * 60000),
        severity: 'medium'
      },
      {
        id: '4',
        type: 'line_move',
        title: 'Significant Line Movement',
        description: 'Warriors spread: -4 → -5.5 (Injury: Curry questionable)',
        timestamp: new Date(Date.now() - 45 * 60000),
        severity: 'medium'
      },
      {
        id: '5',
        type: 'steam',
        title: 'Morning Steam',
        description: 'Cardinals -3 moved to -3.5 as sharp action came in',
        timestamp: new Date(Date.now() - 120 * 60000),
        severity: 'low'
      },
    ]
    setAlerts(mockAlerts)
  }, [])

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'high': return 'bg-red-900 border-red-700 text-red-100'
      case 'medium': return 'bg-amber-900 border-amber-700 text-amber-100'
      case 'low': return 'bg-slate-800 border-slate-700 text-slate-300'
      default: return 'bg-slate-800'
    }
  }

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'steam': return '🔥'
      case 'rlm': return '↩️'
      case 'odds_boost': return '⚡'
      case 'line_move': return '📊'
      default: return '📢'
    }
  }

  const getTypeLabel = (type: string) => {
    switch (type) {
      case 'steam': return 'Steam'
      case 'rlm': return 'RLM'
      case 'odds_boost': return 'Boost'
      case 'line_move': return 'Line Move'
      default: return 'Alert'
    }
  }

  const formatTime = (date: Date) => {
    const now = new Date()
    const diff = now.getTime() - date.getTime()
    const minutes = Math.floor(diff / 60000)
    
    if (minutes < 1) return 'Just now'
    if (minutes < 60) return `${minutes}m ago`
    
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return `${hours}h ago`
    
    return date.toLocaleDateString()
  }

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white mb-2">Real-Time Alerts</h1>
        <p className="text-slate-400">Steam moves, RLM, odds boosts, and significant line movements</p>
      </div>

      {/* Alert Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-slate-400 text-sm">Today</p>
          <p className="text-2xl font-bold text-white">{alerts.length}</p>
        </div>
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-slate-400 text-sm">High Priority</p>
          <p className="text-2xl font-bold text-red-400">{alerts.filter(a => a.severity === 'high').length}</p>
        </div>
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-slate-400 text-sm">Medium Priority</p>
          <p className="text-2xl font-bold text-amber-400">{alerts.filter(a => a.severity === 'medium').length}</p>
        </div>
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-slate-400 text-sm">Last Alert</p>
          <p className="text-sm font-semibold text-cyan-400">{formatTime(alerts[0]?.timestamp || new Date())}</p>
        </div>
      </div>

      {/* Alerts List */}
      <div className="space-y-3">
        {alerts.map(alert => (
          <div
            key={alert.id}
            className={`rounded-lg border p-4 ${getSeverityColor(alert.severity)}`}
          >
            <div className="flex items-start gap-4">
              <span className="text-2xl">{getTypeIcon(alert.type)}</span>
              <div className="flex-1">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-semibold">{alert.title}</h3>
                  <span className="text-xs font-medium px-2 py-1 bg-black/20 rounded">
                    {getTypeLabel(alert.type)}
                  </span>
                </div>
                <p className="text-sm opacity-90">{alert.description}</p>
                <p className="text-xs opacity-75 mt-2">{formatTime(alert.timestamp)}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Empty State */}
      {alerts.length === 0 && (
        <div className="text-center py-12 bg-slate-900 rounded-lg border border-slate-800">
          <p className="text-slate-400">No alerts at this time</p>
          <p className="text-slate-500 text-sm mt-2">Alerts will appear here when market activity is detected</p>
        </div>
      )}
    </div>
  )
}
