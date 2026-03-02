'use client'

import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { generateMockPicks, calculateSharpeRatio, calculateCLV } from '@/lib/algorithms'
import { formatPercent, formatCurrency, getSportEmoji, groupBy, cn } from '@/lib/utils'
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts'
import { useState } from 'react'

export default function AnalyticsPage() {
  const [timeframe, setTimeframe] = useState<'7d' | '30d' | 'all'>('30d')

  // Fetch picks
  const { data: picks = [] } = useQuery({
    queryKey: ['picks', 'analytics'],
    queryFn: async () => {
      try {
        const result = await api.getPicks()
        return result.data || generateMockPicks(50)
      } catch {
        return generateMockPicks(50)
      }
    },
  })

  // Calculate comprehensive stats
  const completedPicks = picks.filter((p: any) => p.status !== 'pending')
  const wins = completedPicks.filter((p: any) => p.status === 'won')
  const losses = completedPicks.filter((p: any) => p.status === 'lost')

  const stats = {
    totalPicks: picks.length,
    completedPicks: completedPicks.length,
    wins: wins.length,
    losses: losses.length,
    winRate: completedPicks.length
      ? Math.round((wins.length / completedPicks.length) * 100)
      : 0,
    avgConfidence: picks.length
      ? Math.round(picks.reduce((sum: number, p: any) => sum + p.confidence, 0) / picks.length)
      : 0,
    roi: completedPicks.length ? calculateROI(completedPicks) : 0,
    sharpeRatio: completedPicks.length ? calculateSharpeRatio(generateReturns(completedPicks)) : 0,
    clvAverage: completedPicks.length
      ? Math.round(
          completedPicks.reduce((sum: number, p: any) => {
            return sum + (p.closingLineValue || 0)
          }, 0) / completedPicks.length * 100
        ) / 100
      : 0,
  }

  // Data by sport
  const bySort = groupBy(completedPicks, 'sport')
  const sportData = Object.entries(bySort).map(([sport, sportPicks]) => {
    const sportWins = sportPicks.filter((p: any) => p.status === 'won').length
    return {
      name: sport,
      icon: getSportEmoji(sport),
      picks: sportPicks.length,
      wins: sportWins,
      losses: sportPicks.length - sportWins,
      winRate: sportPicks.length ? Math.round((sportWins / sportPicks.length) * 100) : 0,
    }
  })

  // Confidence distribution
  const confidenceDistribution = [
    {
      name: '50-60%',
      count: picks.filter((p: any) => p.confidence >= 50 && p.confidence < 60).length,
    },
    {
      name: '60-70%',
      count: picks.filter((p: any) => p.confidence >= 60 && p.confidence < 70).length,
    },
    {
      name: '70-80%',
      count: picks.filter((p: any) => p.confidence >= 70 && p.confidence < 80).length,
    },
    {
      name: '80-90%',
      count: picks.filter((p: any) => p.confidence >= 80 && p.confidence < 90).length,
    },
    {
      name: '90%+',
      count: picks.filter((p: any) => p.confidence >= 90).length,
    },
  ]

  const colors = ['#00D9FF', '#22C55E', '#EAB308', '#EF4444', '#8B5CF6']

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-foreground">Performance Analytics</h1>
        <p className="text-muted-foreground">Deep dive into your betting performance and signal calibration</p>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">Win Rate</div>
          <div className="mt-2 text-2xl font-bold text-success">{stats.winRate}%</div>
          <div className="text-xs text-muted-foreground mt-1">
            {stats.wins}W / {stats.losses}L
          </div>
        </div>
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">ROI</div>
          <div className="mt-2 text-2xl font-bold text-primary">{stats.roi}%</div>
          <div className="text-xs text-muted-foreground mt-1">Profit margin</div>
        </div>
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">Sharpe Ratio</div>
          <div className="mt-2 text-2xl font-bold text-foreground">
            {stats.sharpeRatio.toFixed(2)}
          </div>
          <div className="text-xs text-muted-foreground mt-1">Risk-adjusted return</div>
        </div>
        <div className="rounded-lg border border-muted bg-muted/50 p-4">
          <div className="text-sm text-muted-foreground">CLV Average</div>
          <div className="mt-2 text-2xl font-bold text-foreground">
            {formatPercent(stats.clvAverage)}
          </div>
          <div className="text-xs text-muted-foreground mt-1">Closing line value</div>
        </div>
      </div>

      {/* Charts Grid */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Win/Loss by Sport */}
        <div className="rounded-lg border border-muted bg-muted/50 p-6">
          <h3 className="font-semibold text-foreground mb-4">Performance by Sport</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={sportData}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--muted))" />
              <XAxis dataKey="name" stroke="hsl(var(--muted-foreground))" />
              <YAxis stroke="hsl(var(--muted-foreground))" />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--muted))',
                  border: 'none',
                  borderRadius: '8px',
                  color: 'hsl(var(--foreground))',
                }}
              />
              <Legend />
              <Bar dataKey="wins" fill="hsl(var(--success))" name="Wins" />
              <Bar dataKey="losses" fill="hsl(var(--danger))" name="Losses" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Confidence Distribution */}
        <div className="rounded-lg border border-muted bg-muted/50 p-6">
          <h3 className="font-semibold text-foreground mb-4">Confidence Distribution</h3>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={confidenceDistribution}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={({ name, count }) => `${name}: ${count}`}
                outerRadius={100}
                fill="#8884d8"
                dataKey="count"
              >
                {colors.map((color, index) => (
                  <Cell key={`cell-${index}`} fill={color} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--muted))',
                  border: 'none',
                  borderRadius: '8px',
                  color: 'hsl(var(--foreground))',
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Sports Performance Table */}
      <div className="rounded-lg border border-muted bg-muted/50 p-6 space-y-4">
        <h3 className="font-semibold text-foreground">Detailed Sport Performance</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-muted">
                <th className="text-left py-3 px-4 text-muted-foreground">Sport</th>
                <th className="text-center py-3 px-4 text-muted-foreground">Picks</th>
                <th className="text-center py-3 px-4 text-muted-foreground">Wins</th>
                <th className="text-center py-3 px-4 text-muted-foreground">Losses</th>
                <th className="text-center py-3 px-4 text-muted-foreground">Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {sportData.map((sport) => (
                <tr key={sport.name} className="border-b border-muted/50 hover:bg-muted/50">
                  <td className="py-3 px-4">
                    <span className="mr-2">{sport.icon}</span>
                    {sport.name}
                  </td>
                  <td className="text-center py-3 px-4 text-foreground">{sport.picks}</td>
                  <td className="text-center py-3 px-4">
                    <span className="text-success font-medium">{sport.wins}</span>
                  </td>
                  <td className="text-center py-3 px-4">
                    <span className="text-danger font-medium">{sport.losses}</span>
                  </td>
                  <td className="text-center py-3 px-4">
                    <span
                      className={cn(
                        'font-bold',
                        sport.winRate >= 55
                          ? 'text-success'
                          : sport.winRate >= 50
                            ? 'text-primary'
                            : 'text-danger'
                      )}
                    >
                      {sport.winRate}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Export Data */}
      <div className="flex gap-3">
        <button className="flex-1 btn-secondary">Export Performance Report 📊</button>
        <button className="flex-1 btn-primary">Reset Analytics ⚠️</button>
      </div>
    </div>
  )
}

function calculateROI(picks: any[]): number {
  if (picks.length === 0) return 0
  const totalStaked = picks.reduce((sum, p) => sum + (p.recommendedUnits || 10), 0)
  const totalWinnings = picks
    .filter((p: any) => p.status === 'won')
    .reduce((sum, p) => {
      const profit = p.recommendedUnits * ((p.currentOdds + 100) / 100 - 1)
      return sum + profit
    }, 0)
  return Math.round((totalWinnings / totalStaked) * 100 * 10) / 10
}

function generateReturns(picks: any[]): number[] {
  return picks.map((p) => (p.status === 'won' ? 1 : -1))
}
