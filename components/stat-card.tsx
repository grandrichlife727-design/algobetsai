'use client'

interface StatCardProps {
  label: string
  value: string | number
  subtext?: string
  icon?: string
  trend?: 'up' | 'down' | 'neutral'
  change?: number
}

export function StatCard({
  label,
  value,
  subtext,
  icon,
  trend = 'neutral',
  change,
}: StatCardProps) {
  return (
    <div className="rounded-lg border border-muted bg-muted/50 p-6 hover:border-primary/50 transition-all">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-3 flex-1">
          <p className="text-sm text-muted-foreground font-medium">{label}</p>
          <p className="text-3xl font-bold text-foreground">{value}</p>
          {subtext && <p className="text-xs text-muted-foreground">{subtext}</p>}
          {change !== undefined && (
            <div className={`text-xs font-medium ${trend === 'up' ? 'text-success' : trend === 'down' ? 'text-danger' : 'text-muted-foreground'}`}>
              {trend === 'up' && '↑'} {trend === 'down' && '↓'} {Math.abs(change)}%
            </div>
          )}
        </div>
        {icon && <div className="text-4xl">{icon}</div>}
      </div>
    </div>
  )
}
