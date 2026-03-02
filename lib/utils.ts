import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDate(timestamp: number): string {
  return new Date(timestamp).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatOdds(odds: number): string {
  if (odds > 0) {
    return `+${odds}`
  }
  return odds.toString()
}

export function formatCurrency(amount: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount)
}

export function formatPercent(value: number, decimals: number = 1): string {
  return `${(value).toFixed(decimals)}%`
}

export function formatUnits(units: number): string {
  return units.toFixed(2)
}

export function calculateDecimalOdds(americanOdds: number): number {
  if (americanOdds > 0) {
    return (americanOdds + 100) / 100
  } else {
    return 100 / (Math.abs(americanOdds)) + 1
  }
}

export function calculateProfit(stake: number, odds: number): number {
  const decimal = calculateDecimalOdds(odds)
  return stake * (decimal - 1)
}

export function getConfidenceColor(confidence: number): string {
  if (confidence >= 80) return 'text-success'
  if (confidence >= 70) return 'text-primary'
  if (confidence >= 60) return 'text-warning'
  return 'text-danger'
}

export function getConfidenceBgColor(confidence: number): string {
  if (confidence >= 80) return 'bg-success/20'
  if (confidence >= 70) return 'bg-primary/20'
  if (confidence >= 60) return 'bg-warning/20'
  return 'bg-danger/20'
}

export function getSportEmoji(sport: string): string {
  const emojis: Record<string, string> = {
    NFL: '🏈',
    NBA: '🏀',
    MLB: '⚾',
    NHL: '🏒',
    CFB: '🏈',
    CBB: '🏀',
    WNBA: '🏀',
    MLS: '⚽',
  }
  return emojis[sport] || '🎯'
}

export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null

  return function executedFunction(...args: Parameters<T>) {
    const later = () => {
      timeout = null
      func(...args)
    }

    if (timeout) {
      clearTimeout(timeout)
    }
    timeout = setTimeout(later, wait)
  }
}

export function throttle<T extends (...args: any[]) => any>(
  func: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle: boolean = false

  return function (...args: Parameters<T>) {
    if (!inThrottle) {
      func(...args)
      inThrottle = true
      setTimeout(() => (inThrottle = false), limit)
    }
  }
}

export function groupBy<T>(array: T[], key: keyof T): Record<string, T[]> {
  return array.reduce(
    (result, item) => {
      const group = String(item[key])
      if (!result[group]) {
        result[group] = []
      }
      result[group].push(item)
      return result
    },
    {} as Record<string, T[]>
  )
}

export function sortByConfidence<T extends { confidence: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => b.confidence - a.confidence)
}

export function filterByStatus<T extends { status: string }>(
  items: T[],
  status: string
): T[] {
  return items.filter((item) => item.status === status)
}
