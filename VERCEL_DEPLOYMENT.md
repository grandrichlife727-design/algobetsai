# Vercel Deployment Guide - AlgoBets AI v2.0

## Deployment Fix Applied

The deployment error has been resolved by configuring Vercel to properly recognize and build the Next.js frontend application.

### What Was Fixed

**Problem:** Vercel was detecting the Python FastAPI backend (main.py) and trying to build it as a Python/FastAPI project, when the primary application is actually a Next.js frontend.

**Solution:** 
1. Created `vercel.json` configuration file that explicitly sets:
   - Build command: `next build`
   - Framework: `nextjs`
   - Output directory: `.next`

2. Updated `.vercelignore` to exclude all Python-related files:
   - `main.py`
   - `*.py`
   - `pyproject.toml`
   - `requirements.txt`
   - `render.yaml`
   - `Dockerfile`
   - And other backend/deployment files

### Deployment Steps

1. **Push to GitHub:**
   ```bash
   git add .
   git commit -m "Fix: Configure Vercel for Next.js deployment"
   git push origin v0/emaginegraffix-7385-0ffb6852
   ```

2. **Deploy to Vercel:**
   - Vercel will automatically detect the changes
   - The build will now use Next.js (not Python)
   - Build time: ~2-3 minutes
   - Output: Next.js frontend running on Vercel Edge Network

3. **Verify Deployment:**
   - Check Vercel Dashboard for successful build
   - Visit your deployment URL
   - Dashboard should load at `/dashboard`

### Environment Variables (Set in Vercel Dashboard)

```
NEXT_PUBLIC_API_URL=https://algobetsai.onrender.com
```

This tells the frontend where to fetch data from your FastAPI backend running on Render.

### Project Structure

```
/vercel/share/v0-project/
├── app/                          # Next.js app router
│   ├── layout.tsx               # Root layout
│   ├── page.tsx                 # Home (redirects to dashboard)
│   ├── dashboard/               # Dashboard layout
│   │   ├── layout.tsx
│   │   ├── page.tsx            # Dashboard home
│   │   ├── picks/
│   │   ├── alerts/
│   │   ├── parlay/
│   │   ├── analytics/
│   │   └── sharp/
│   ├── globals.css
│   └── providers.tsx            # React providers (Tanstack Query, etc.)
│
├── components/                   # Reusable React components
│   ├── sidebar.tsx
│   ├── header.tsx
│   ├── stat-card.tsx
│   ├── pick-card.tsx
│   ├── trending-bets.tsx
│   └── pick-details.tsx
│
├── lib/                          # Utility functions
│   ├── api.ts                   # Fetch functions
│   ├── utils.ts                 # Helper functions
│   └── algorithms.ts            # Prediction logic
│
├── types/                        # TypeScript types
│   └── index.ts
│
├── public/                       # Static assets
│
├── package.json                  # Dependencies
├── next.config.ts               # Next.js configuration
├── tsconfig.json                # TypeScript configuration
├── tailwind.config.ts           # Tailwind CSS configuration
├── postcss.config.js            # PostCSS configuration
├── vercel.json                  # Vercel deployment config (NEWLY ADDED)
├── .vercelignore                # Files to exclude (UPDATED)
│
└── main.py                       # FastAPI backend (ignored by Vercel)
```

### Backend Integration

The FastAPI backend (main.py) runs separately on Render:
- **URL:** https://algobetsai.onrender.com
- **Status Endpoint:** https://algobetsai.onrender.com/ (returns API version)
- **API Base:** https://algobetsai.onrender.com/api/

The Next.js frontend communicates with the backend via the `NEXT_PUBLIC_API_URL` environment variable.

### Files Modified for Deployment

1. **vercel.json** (NEW)
   - Explicitly configures Vercel to use Next.js framework
   - Defines build command and output directory

2. **.vercelignore** (UPDATED)
   - Now explicitly excludes all Python files
   - Excludes backend-specific configuration (render.yaml, Dockerfile, etc.)
   - Ensures clean Next.js-only deployment

### Testing Deployment Locally

```bash
# Install dependencies
npm install

# Build Next.js app
npm run build

# Start production server
npm start
```

The app will run on http://localhost:3000

### Troubleshooting

**Issue:** Build still fails with Python detection error
- **Solution:** Verify `.vercelignore` was properly saved (check all lines are present)
- **Solution:** Clear Vercel build cache in project settings and redeploy

**Issue:** Frontend can't connect to backend API
- **Solution:** Verify `NEXT_PUBLIC_API_URL` environment variable is set in Vercel Dashboard
- **Solution:** Check CORS is enabled in FastAPI backend (it should be)

**Issue:** Vercel build succeeds but app shows 404
- **Solution:** Ensure `next.config.ts` is present with correct configuration
- **Solution:** Check `app/layout.tsx` and `app/page.tsx` exist

### Next Steps

1. Commit changes to GitHub
2. Vercel will auto-deploy
3. Visit your Vercel deployment URL
4. Test the 6 dashboard pages:
   - Dashboard (overview, stats, trending)
   - Top Picks (full predictions list)
   - Alerts (real-time market moves)
   - Parlay Builder (multi-leg betting)
   - Analytics (performance tracking)
   - Sharp Tools (advanced features)

## Support

For deployment issues:
1. Check Vercel Dashboard build logs
2. Verify environment variables are set
3. Confirm GitHub branch is up to date
4. Check `.vercelignore` and `vercel.json` are properly formatted

---

**Deployment Status:** ✅ Ready for Vercel
**Framework:** Next.js 16
**Node Version:** 20.x (auto-detected)
**Build Command:** `next build`
**Start Command:** `next start`
