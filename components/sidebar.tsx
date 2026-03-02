'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'

interface SidebarProps {
  open: boolean
  onToggle: (open: boolean) => void
}

const navItems = [
  { href: '/dashboard', label: 'Dashboard', icon: 'D' },
  { href: '/dashboard/picks', label: 'Top Picks', icon: 'P' },
  { href: '/dashboard/alerts', label: 'Alerts', icon: 'A' },
  { href: '/dashboard/parlay', label: 'Parlay Builder', icon: 'B' },
  { href: '/dashboard/analytics', label: 'Analytics', icon: 'G' },
  { href: '/dashboard/sharp', label: 'Sharp Tools', icon: 'S' },
]

export function Sidebar({ open, onToggle }: SidebarProps) {
  const pathname = usePathname()

  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => onToggle(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          'fixed left-0 top-0 z-50 h-screen w-64 bg-background border-r border-muted transition-all duration-300 md:relative md:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-muted">
          <div className="flex items-center gap-2">
            <div className="text-2xl">🎯</div>
            <div className="font-bold text-lg text-foreground">AlgoBets</div>
          </div>
          <button
            onClick={() => onToggle(false)}
            className="md:hidden text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-4 space-y-2 overflow-auto">
          {navItems.map((item) => {
            const isActive = item.href === '/dashboard'
              ? pathname === '/dashboard'
              : pathname === item.href || pathname.startsWith(item.href + '/')
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => onToggle(false)}
                className={cn(
                  'flex items-center gap-3 px-4 py-3 rounded-lg transition-colors',
                  isActive
                    ? 'bg-primary/20 text-primary font-medium'
                    : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'
                )}
              >
                <span className="text-xl w-6 text-center">{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="p-4 border-t border-muted space-y-2">
          <button className="w-full rounded-lg border border-primary/50 py-2 px-4 text-sm text-primary hover:bg-primary/10 transition-colors">
            Settings ⚙️
          </button>
          <button className="w-full rounded-lg border border-muted py-2 px-4 text-sm text-muted-foreground hover:bg-muted/50 transition-colors">
            Sign Out
          </button>
        </div>
      </aside>
    </>
  )
}
