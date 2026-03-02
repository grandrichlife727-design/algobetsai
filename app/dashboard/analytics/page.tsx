'use client'

export default function AnalyticsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-slate-100">Performance Analytics</h1>
        <p className="text-slate-400 mt-1">Comprehensive tracking and ROI analysis</p>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: 'Total Picks', value: '247', subtext: 'all time', color: 'cyan' },
          { label: 'Win Rate', value: '62.4%', subtext: 'vs 52.4% expected', color: 'emerald' },
          { label: 'Units Won', value: '+23.4', subtext: '$2,340 @ $100/unit', color: 'emerald' },
          { label: 'Sharpe Ratio', value: '1.82', subtext: 'strong risk-adjusted returns', color: 'amber' },
        ].map((metric, idx) => (
          <div key={idx} className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
            <p className="text-slate-400 text-sm mb-2">{metric.label}</p>
            <p className={`text-3xl font-bold text-${metric.color}-400`}>{metric.value}</p>
            <p className="text-xs text-slate-500 mt-2">{metric.subtext}</p>
          </div>
        ))}
      </div>

      {/* Performance by Sport */}
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
        <h3 className="font-semibold text-slate-100 mb-4">Performance by Sport</h3>
        
        <div className="space-y-4">
          {[
            { sport: 'NFL', picks: 98, wins: 62, roi: '+24.3%', color: 'emerald' },
            { sport: 'NBA', picks: 75, wins: 47, roi: '+18.2%', color: 'emerald' },
            { sport: 'MLB', picks: 42, wins: 25, roi: '+8.1%', color: 'amber' },
            { sport: 'NHL', picks: 32, wins: 17, roi: '+2.4%', color: 'amber' },
          ].map((sport) => (
            <div key={sport.sport} className="p-4 rounded-lg bg-slate-800/50 border border-slate-700">
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold text-slate-100">{sport.sport}</span>
                <span className={`text-lg font-bold text-${sport.color}-400`}>{sport.roi}</span>
              </div>
              
              <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                <div
                  className={`h-full bg-gradient-to-r from-${sport.color}-500 to-${sport.color}-400`}
                  style={{ width: `${(sport.wins / sport.picks) * 100}%` }}
                />
              </div>
              
              <div className="flex items-center justify-between mt-2 text-xs text-slate-400">
                <span>{sport.wins}W / {sport.picks - sport.wins}L</span>
                <span>{((sport.wins / sport.picks) * 100).toFixed(1)}% WR</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Confidence Calibration */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
          <h3 className="font-semibold text-slate-100 mb-4">Confidence Calibration</h3>
          
          <div className="space-y-3">
            {[
              { range: '80-100%', actual: 78, expected: 82, diff: -4 },
              { range: '70-79%', actual: 65, expected: 72, diff: -7 },
              { range: '60-69%', actual: 54, expected: 65, diff: -11 },
              { range: '50-59%', actual: 48, expected: 54, diff: -6 },
            ].map((cal) => (
              <div key={cal.range} className="p-3 rounded bg-slate-800/50">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-slate-300">{cal.range}</span>
                  <span className="text-xs text-slate-400">
                    Actual: <span className="text-cyan-400 font-semibold">{cal.actual}%</span>
                  </span>
                </div>
                <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-cyan-500"
                    style={{ width: `${cal.actual}%` }}
                  />
                </div>
              </div>
            ))}
          </div>

          <p className="text-xs text-slate-500 mt-4">
            Model is slightly optimistic - slight adjustment recommended
          </p>
        </div>

        {/* CLV Analysis */}
        <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
          <h3 className="font-semibold text-slate-100 mb-4">CLV (Closing Line Value)</h3>
          
          <div className="space-y-3">
            <div>
              <p className="text-sm text-slate-400 mb-2">Positive CLV: 189 picks</p>
              <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                <div className="h-full bg-emerald-500" style={{ width: '77%' }} />
              </div>
              <p className="text-xs text-slate-500 mt-1">+2.3% average edge</p>
            </div>
            
            <div>
              <p className="text-sm text-slate-400 mb-2">Negative CLV: 58 picks</p>
              <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
                <div className="h-full bg-red-500" style={{ width: '23%' }} />
              </div>
              <p className="text-xs text-slate-500 mt-1">-1.8% average disadvantage</p>
            </div>

            <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/50 mt-4">
              <p className="text-sm text-emerald-300">
                <span className="font-semibold">Strong CLV Performance:</span> Consistently getting favorable closing lines
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Export Data */}
      <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-6">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-slate-100">Export Data</h3>
            <p className="text-sm text-slate-400 mt-1">Download your complete history for external analysis</p>
          </div>
          <button className="px-6 py-3 rounded-lg bg-cyan-500/20 border border-cyan-500/50 text-cyan-300 hover:bg-cyan-500/30 font-medium transition-colors">
            Download CSV
          </button>
        </div>
      </div>
    </div>
  )
}
