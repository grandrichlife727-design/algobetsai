'use client'

const mockAlerts = [
  {
    id: 1,
    type: 'steam',
    title: 'Steam Move Detected',
    event: 'Cowboys -3.5 → -4',
    description: 'Sharp money detected moving line at sharp books',
    timestamp: '2 minutes ago',
    severity: 'high',
  },
  {
    id: 2,
    type: 'rlm',
    title: 'Reverse Line Movement',
    event: 'Lakers Over 218.5 at -105 → -120',
    description: 'Public money on Under but professionals on Over',
    timestamp: '5 minutes ago',
    severity: 'high',
  },
  {
    id: 3,
    type: 'odds_boost',
    title: 'Odds Boost Available',
    event: 'Chiefs -2 at +200 (DraftKings)',
    description: 'Promotional boost available - highest current line',
    timestamp: '8 minutes ago',
    severity: 'medium',
  },
  {
    id: 4,
    type: 'line_move',
    title: 'Significant Line Move',
    event: '49ers +5 → +4.5',
    description: 'Line moved 0.5 points across multiple books',
    timestamp: '12 minutes ago',
    severity: 'medium',
  },
  {
    id: 5,
    type: 'clv_hit',
    title: 'CLV Edge Alert',
    event: 'Celtics -7 opened at -6.5',
    description: 'Potential closing line value opportunity detected',
    timestamp: '15 minutes ago',
    severity: 'low',
  },
]

export default function AlertsPage() {
  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'high':
        return 'bg-red-500/20 border-red-500/50 text-red-300'
      case 'medium':
        return 'bg-amber-500/20 border-amber-500/50 text-amber-300'
      case 'low':
        return 'bg-blue-500/20 border-blue-500/50 text-blue-300'
      default:
        return 'bg-slate-500/20 border-slate-500/50 text-slate-300'
    }
  }

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'steam':
        return '💨'
      case 'rlm':
        return '⚡'
      case 'odds_boost':
        return '📈'
      case 'line_move':
        return '📊'
      case 'clv_hit':
        return '🎯'
      default:
        return '🔔'
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-slate-100">Real-Time Alerts</h1>
        <p className="text-slate-400 mt-1">Live market movements and opportunities</p>
      </div>

      <div className="space-y-3">
        {mockAlerts.map((alert) => (
          <div
            key={alert.id}
            className={`rounded-lg border p-4 ${getSeverityColor(alert.severity)} backdrop-blur-sm hover:shadow-lg transition-shadow cursor-pointer`}
          >
            <div className="flex items-start gap-4">
              <div className="text-2xl">{getTypeIcon(alert.type)}</div>
              
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <h3 className="font-semibold">{alert.title}</h3>
                  <span className="text-xs opacity-75">{alert.timestamp}</span>
                </div>
                
                <p className="font-mono text-sm font-bold mb-1">{alert.event}</p>
                <p className="text-sm opacity-90">{alert.description}</p>
              </div>
              
              <button className="px-3 py-1 rounded bg-white/10 hover:bg-white/20 text-sm font-medium transition-colors whitespace-nowrap">
                Take Action
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Alert Settings */}
      <div className="mt-8 rounded-lg border border-slate-700 bg-slate-900/50 p-6">
        <h3 className="font-semibold text-slate-100 mb-4">Alert Preferences</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {['Steam Moves', 'RLM', 'Odds Boosts', 'Line Moves', 'CLV Alerts', 'Injury News'].map((pref) => (
            <label key={pref} className="flex items-center gap-3 cursor-pointer">
              <input type="checkbox" defaultChecked className="w-4 h-4 rounded" />
              <span className="text-slate-300">{pref}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  )
}
