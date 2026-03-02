# AlgoBets AI v2.0 - Quick Start Guide

Get up and running in 5 minutes.

## Fastest Path to Running App

### 1. Install Dependencies (30 seconds)

```bash
npm install
# or: pnpm install / yarn install / bun install
```

### 2. Create Environment File (10 seconds)

Create `.env.local`:
```env
NEXT_PUBLIC_API_URL=https://algobetsai.onrender.com
```

### 3. Start Development Server (20 seconds)

```bash
npm run dev
```

### 4. Open in Browser (5 seconds)

Visit: **http://localhost:3000**

You're done! The app loads with mock data and is fully functional.

---

## What You Get

### Immediate Features
✅ Dashboard with performance metrics  
✅ Top Picks with AI confidence scores  
✅ Real-Time Alerts system  
✅ Parlay Builder with Kelly sizing  
✅ Performance Analytics dashboard  
✅ Sharp Tools for professionals  

### Mock Data
- 25 realistic AI picks with multi-factor signals
- Performance history and ROI tracking
- Confidence breakdowns by signal type
- Multi-sport examples (NFL, NBA, MLB, NHL)

### Live API Integration
- When backend is available, real data loads automatically
- Seamless fallback to mock data if API unavailable
- All features work with both mock and real data

---

## First Steps to Explore

### 1. View Dashboard
Home page shows your overview:
- 4 key metrics (Total Picks, Elite Picks, Avg Confidence, ROI)
- Top 5 picks by confidence
- Performance by sport
- Trending bets

### 2. Check Top Picks
See all predictions with advanced filtering:
- Sort by: Confidence, Edge, or Kelly sizing
- Filter by: Sport, Status (Pending/Won/Lost)
- Search by: Team name or event
- Click any pick for detailed breakdown

### 3. View Real-Time Alerts
Monitor for:
- Steam moves (sharp money activity)
- Reverse line movements (sharp vs public)
- Odds boosts (value opportunities)
- Line changes (significant shifts)

### 4. Build a Parlay
Combine picks with intelligence:
- See correlation risk (low/medium/high)
- View potential payouts
- Get Kelly-adjusted stake recommendations
- Check combined confidence across legs

### 5. Review Performance
Deep analytics dashboard:
- Win rate and ROI by sport
- Confidence distribution
- Sharpe ratio (risk-adjusted returns)
- Closing Line Value tracking

### 6. Customize in Sharp Tools
Professional configuration:
- Adjust signal weights in prediction model
- Choose odds devig method
- View API documentation
- Join optional leaderboard

---

## Key Concepts

### Confidence Score (52-99%)
Weighted combination of 6 signals:
- **CLV Edge** (25%) - Closing line value advantage
- **Sharp Money** (20%) - Professional action detection  
- **Line Movement** (20%) - Significant odds shifts
- **Expert Consensus** (15%) - Multiple sources agree
- **Odds Quality** (10%) - Best available odds
- **Injury News** (10%) - Real-time event impact

**Higher = better prediction edge**

### Kelly Criterion Sizing
Math-based stake recommendations:
- **Full Kelly**: Aggressive sizing (can be risky)
- **¼ Kelly**: Recommended for bankroll safety
- Based on confidence and available odds
- Adjusts per pick

**Use ¼ Kelly for stable growth**

### Edge Percentage
How much your pick differs from implied probability:
- **+2% edge**: You picked 75%, market says 73%
- **Positive edge**: You have an advantage
- **Negative edge**: Market got better odds

**Positive edge = good picks**

---

## Connecting to Live API

When your backend is running:

```env
NEXT_PUBLIC_API_URL=https://your-backend-url.com
```

Features that enhance with live data:
- Real AI predictions updated continuously
- Live odds from multiple sportsbooks
- Real-time alerts from sharp money flows
- Actual performance history
- Calibrated confidence scores

---

## Development Workflows

### Make Changes
Edit any file in `/app` or `/components`:
```bash
# Edit a page
# → Browser auto-updates (HMR)
# → See changes instantly
```

### Add a New Page
```bash
# Create /app/(dashboard)/new-page/page.tsx
# Sidebar auto-updates
# Add components and data fetching
```

### Debug in Browser
```bash
# Open DevTools: F12
# Network tab: see API calls
# Console: check for errors
# React DevTools: inspect components
```

---

## Common Tasks

### Change Colors
Edit `/app/globals.css`:
```css
--primary: #00D9FF;      /* Primary cyan */
--success: #22C55E;      /* Success green */
--warning: #EAB308;      /* Warning amber */
--danger: #EF4444;       /* Danger red */
```

### Update API Endpoint
Edit `.env.local`:
```env
NEXT_PUBLIC_API_URL=your-new-url
```

### Customize Pick Display
Edit `/components/pick-card.tsx`:
- Change confidence gauge styling
- Add/remove signal tags
- Modify modal layout

### Add New Algorithm
Edit `/lib/algorithms.ts`:
- Add calculation function
- Export from module
- Use in components

---

## Troubleshooting

### App won't start
```bash
# Clear node_modules and reinstall
rm -rf node_modules
npm install

# Try dev server again
npm run dev
```

### API connection failed
- Check `.env.local` has correct `NEXT_PUBLIC_API_URL`
- Verify backend is running
- Look for CORS errors in browser console
- App should fall back to mock data anyway

### Charts not showing
- Check browser console for errors
- Ensure Recharts installed: `npm ls recharts`
- Verify data is loading (Network tab)

### Slow performance
- Check Network tab for slow API calls
- Use React DevTools Profiler
- Build and test: `npm run build && npm start`

---

## Next Steps

### 1. Customize for Your Brand
- Change colors and fonts
- Add your logo
- Customize pick signals

### 2. Connect to Your Backend
- Update `NEXT_PUBLIC_API_URL`
- Ensure backend endpoints match `/lib/api.ts`
- Test each endpoint

### 3. Deploy
**Easiest**: Vercel (see DEPLOYMENT.md)
```bash
git push
# → Auto-deploys
```

**Docker**:
```bash
docker build -t algobets-ai .
docker run -p 3000:3000 algobets-ai
```

### 4. Add Features
- WebSocket for real-time updates
- Authentication / User accounts
- Database for user preferences
- Email alerts
- Mobile app

---

## File Structure Guide

```
app/                    # Pages and layouts
  (dashboard)/          # Dashboard group
    page.tsx            # Dashboard home ← START HERE
    picks/              # Top Picks page
    alerts/             # Alerts page
    parlay/             # Parlay Builder
    analytics/          # Performance
    sharp/              # Sharp Tools

components/            # Reusable UI
  pick-card.tsx        # Individual pick display
  pick-details.tsx     # Modal details
  sidebar.tsx          # Navigation
  stat-card.tsx        # Metric cards

lib/                   # Core logic
  api.ts              # API client
  algorithms.ts       # Predictions
  utils.ts            # Helpers
```

---

## Performance Tips

- Use `npm run build` to test production build locally
- Enable browser DevTools → Performance tab
- Check bundle size: `npm run build`
- Use React DevTools Profiler to find slow components

---

## Need Help?

1. **Setup issues**: Check `FRONTEND.md` Troubleshooting
2. **API issues**: Check `README.md` for API endpoints
3. **Deployment**: See `DEPLOYMENT.md` for hosting options
4. **Code questions**: Check inline comments in files

---

## Production Checklist

Before deploying:
- [ ] Test on mobile (DevTools → Toggle Device Toolbar)
- [ ] Check all 6 pages load correctly
- [ ] Test API fallback (disconnect backend)
- [ ] Verify environment variables set
- [ ] Run `npm run build` successfully
- [ ] Test production build locally
- [ ] Clear browser cache and test again
- [ ] Check Lighthouse score (DevTools → Lighthouse)

---

**Status**: Ready to develop  
**Time to first features**: ~2 hours  
**Time to production**: ~1 day  
**Maintenance**: Low (leverages Vercel/Render)

Let's build something great! 🚀
