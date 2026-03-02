'use client'

import { useState } from 'react'

export default function AnalyticsPage() {
  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white mb-2">Performance Analytics</h1>
        <p className="text-slate-400">Detailed tracking of wins, losses, ROI, and confidence calibration</p>
      </div>

      {/* Overview Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-sm text-slate-400">Total Picks</p>
          <p className="text-3xl font-bold text-white mt-2">247</p>
          <p className="text-xs text-slate-500 mt-1">This year</p>
        </div>
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-sm text-slate-400">Win Rate</p>
          <p className="text-3xl font-bold text-emerald-400 mt-2">56.3%</p>
          <p className="text-xs text-slate-500 mt-1">Above 50% baseline</p>
        </div>
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-sm text-slate-400">Net Profit</p>
          <p className="text-3xl font-bold text-cyan-400 mt-2">+$1,247</p>
          <p className="text-xs text-slate-500 mt-1">ROI: +12.5%</p>
        </div>
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-4">
          <p className="text-sm text-slate-400">Sharpe Ratio</p>
          <p className="text-3xl font-bold text-amber-400 mt-2">1.89</p>
          <p className="text-xs text-slate-500 mt-1">Risk-adjusted</p>
        </div>
      </div>

      {/* Main Analytics */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* By Sport */}
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Performance by Sport</h2>
          <div className="space-y-3">
            {[
              { sport: 'NBA', wins: 34, losses: 22, roi: 15.2 },
              { sport: 'NFL', wins: 28, losses: 19, roi: 12.8 },
              { sport: 'MLB', wins: 45, losses: 38, roi: 8.4 },
              { sport: 'NCAAF', wins: 22, losses: 18, roi: 10.6 },
              { sport: 'NCAAB', wins: 19, losses: 15, roi: 14.3 }
            ].map((item) => (
              <div key={item.sport} className="flex items-center justify-between p-3 bg-slate-800 rounded">
                <div>
                  <p className="font-medium text-white">{item.sport}</p>
                  <p className="text-xs text-slate-500">{item.wins}W - {item.losses}L</p>
                </div>
                <div className="text-right">
                  <p className={`font-bold ${item.roi >= 10 ? 'text-emerald-400' : 'text-amber-400'}`}>
                    +{item.roi}%
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Confidence Calibration */}
        <div className="bg-slate-900 rounded-lg border border-slate-800 p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Confidence Calibration</h2>
          <p className="text-xs text-slate-400 mb-4">Actual win % vs. predicted confidence</p>
          <div className="space-y-3">
            {[
              { range: '50-60%', predicted: 55, actual: 52, count: 18 },
              { range: '60-70%', predicted: 65, actual: 63, count: 42 },
              { range: '70-80%', predicted: 75, actual: 77, count: 89 },
              { range: '80-90%', predicted: 85, actual: 84, count: 67 },
              { range: '90%+', predicted: 93, actual: 91, count: 31 }
            ].map((item) => (
              <div key={item.range}>
                <div className="flex justify-between items-center mb-2">
                  <span className="text-sm text-slate-400">{item.range}</span>
                  <span className="text-xs text-slate-500">{item.count} picks</span>
                </div>
                <div className="w-full bg-slate-800 rounded-full h-2 relative">
                  <div 
                    className="bg-cyan-500 h-2 rounded-full absolute"
                    style={{ width: `${item.predicted}%` }}
                    title="Predicted"
                  />
                </div>
                <div className="w-full bg-slate-800 rounded-full h-2 relative mt-1">
                  <div 
                    className="bg-emerald-500 h-2 rounded-full absolute"
                    style={{ width: `${item.actual}%` }}
                    title="Actual"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recent Picks Performance */}
      <div className="bg-slate-900 rounded-lg border border-slate-800 p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Recent Picks</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700">
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Pick</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Confidence</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Result</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">ROI</th>
              </tr>
            </thead>
            <tbody>
              {[
                { pick: 'Lakers ML', conf: 78, result: 'Win', roi: '+110' },
                { pick: 'Celtics -5', conf: 72, result: 'Win', roi: '+100' },
                { pick: 'Cowboys -3', conf: 65, result: 'Loss', roi: '-100' },
                { pick: 'Warriors -7', conf: 81, result: 'Win', roi: '+110' },
                { pick: 'Heat +4', conf: 58, result: 'Loss', roi: '-100' },
              ].map((item, i) => (
                <tr key={i} className="border-b border-slate-800 hover:bg-slate-800/50">
                  <td className="py-3 px-4 text-white">{item.pick}</td>
                  <td className="py-3 px-4">
                    <span className="inline-flex items-center px-2 py-1 rounded bg-slate-800 text-cyan-400 text-xs font-medium">
                      {item.conf}%
                    </span>
                  </td>
                  <td className="py-3 px-4">
                    <span className={`font-medium ${item.result === 'Win' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {item.result}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-slate-300">{item.roi}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Export Button */}
      <button className="px-6 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white rounded font-medium">
        📥 Export to CSV
      </button>
    </div>
  )
}
