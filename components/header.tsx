'use client'

interface HeaderProps {
  onMenuClick: () => void
}

export function Header({ onMenuClick }: HeaderProps) {
  return (
    <header className="border-b border-muted bg-muted/20 backdrop-blur-sm sticky top-0 z-30">
      <div className="flex items-center justify-between px-4 md:px-8 py-4">
        {/* Left - Mobile menu */}
        <button
          onClick={onMenuClick}
          className="md:hidden text-foreground hover:text-primary transition-colors p-2"
          aria-label="Open menu"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 6h16M4 12h16M4 18h16"
            />
          </svg>
        </button>

        {/* Center spacer */}
        <div className="flex-1" />

        {/* Right - Actions */}
        <div className="flex items-center gap-4">
          {/* Status indicator */}
          <div className="hidden md:flex items-center gap-2 text-sm text-muted-foreground">
            <span className="w-2 h-2 rounded-full bg-success animate-pulse" />
            Live
          </div>

          {/* Alerts Bell */}
          <button
            className="relative text-foreground hover:text-primary transition-colors p-2"
            aria-label="Notifications"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
              />
            </svg>
            <span className="absolute top-0 right-0 w-2 h-2 rounded-full bg-danger" />
          </button>

          {/* User Avatar */}
          <button className="w-9 h-9 rounded-full bg-primary/20 text-primary font-bold text-sm flex items-center justify-center hover:bg-primary/30 transition-colors">
            U
          </button>
        </div>
      </div>
    </header>
  )
}
