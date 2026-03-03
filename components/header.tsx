'use client'

import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

interface HeaderProps {
  onMenuClick: () => void
}

export function Header({ onMenuClick }: HeaderProps) {
  // Fetch alerts
  const { data: alerts = [] } = useQuery({
    queryKey: ['alerts', 'header'],
    queryFn: async () => {
      try {
        const result = await api.getAlerts(5)
        return result.data || []
      } catch {
        return []
      }
    },
  })

  const unreadAlerts = alerts.filter((a: any) => !a.read).length

  return (
    <header className="border-b border-muted bg-muted/30 backdrop-blur-sm sticky top-0 z-30">
      <div className="flex items-center justify-between px-4 md:px-8 py-4">
        {/* Left - Menu */}
        <button
          onClick={onMenuClick}
          className="md:hidden text-foreground hover:text-primary transition-colors"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>

        {/* Right - Actions */}
        <div className="flex items-center gap-4 ml-auto">
          {/* Alerts Bell */}
          <button className="relative text-foreground hover:text-primary transition-colors p-2">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            {unreadAlerts > 0 && (
              <span className="absolute -top-1 -right-1 inline-flex items-center justify-center px-2 py-1 text-xs font-bold leading-none text-danger-foreground transform translate-x-1/2 -translate-y-1/2 bg-danger rounded-full">
                {unreadAlerts}
              </span>
            )}
          </button>

          {/* User Avatar */}
          <button className="w-10 h-10 rounded-full bg-primary/20 text-primary font-bold flex items-center justify-center hover:bg-primary/30 transition-colors">
            U
          </button>
        </div>
      </div>
    </header>
  )
}
