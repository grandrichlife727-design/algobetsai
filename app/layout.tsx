import type { Metadata, Viewport } from 'next'
import { Providers } from './providers'
import './globals.css'

export const metadata: Metadata = {
  title: 'AlgoBets AI - Sports Betting Intelligence Platform',
  description: 'Enterprise-grade AI-powered sports betting predictions with advanced analytics, real-time alerts, and professional tools.',
  keywords: 'sports betting, AI predictions, odds analysis, betting software, parlay builder, Kelly criterion',
  authors: [{ name: 'AlgoBets AI Team' }],
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: 'https://algobetsai.com',
    siteName: 'AlgoBets AI',
    title: 'AlgoBets AI - Sports Betting Intelligence Platform',
    description: 'Professional sports betting predictions powered by advanced AI algorithms',
    images: [
      {
        url: 'https://algobetsai.com/og-image.png',
        width: 1200,
        height: 630,
      },
    ],
  },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  themeColor: '#00D9FF',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎯</text></svg>" />
      </head>
      <body>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  )
}
