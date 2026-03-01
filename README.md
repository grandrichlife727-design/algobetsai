# Algobets Ai — Backend v4.0

FastAPI backend powering the 7-agent sports betting edge finder.

---

## What's new in v4 (developer summary)

### v3 recap (still in place)
- **Free data layer**: ESPN (games, injuries, props) + Action Network (sharp %, line history) + Pinnacle (CLV benchmark)
- **Odds API**: reserved exclusively for `/api/ev-finder` and `/api/arb-detect` — ~200 credits/month
- **Agent veto system**: 4 kill conditions suppress bad picks before they reach users
- **SQLite pick logging**: every surfaced pick written to `picks.db` on Render persistent disk

### v4 additions

#### 1. CLV Closing Line Capture (automatic)
`_clv_capture_loop` runs every 10 minutes in the background.  
For any logged pick whose game starts within 45 minutes, it fetches Pinnacle's current line and writes it to `clv_pinnacle_close` in the DB.  
**No manual step needed.** The true closing line is captured automatically for every pick.  
This is the gold-standard proof of edge — more meaningful than win rate alone.

#### 2. Platt Confidence Calibration
`fit_calibration()` fits a logistic regression (Platt scaling) on `confidence_raw` vs actual outcomes.  
Kicks in automatically once 200+ resolved picks exist. Refits every 50 new picks.  
After calibration: "75% confidence" genuinely means ~75% historical win rate.  
Before calibration: raw score is used as-is.  
DB stores calibration params in the `calibration` table. Loaded on startup.

#### 3. Weather Signal (NFL + MLB outdoor stadiums)
`fetch_weather_for_game()` calls Open-Meteo (free, no API key) for outdoor stadium games.  
Checks wind speed, temperature, precipitation at game time.  
Thresholds: wind >15mph, temp <20°F, precip >2.5mm.  
`weather_confidence_adjustment()` reduces confidence on totals picks in bad weather.  
Wind >20mph: up to -10 confidence pts on totals. Cold: -5. Rain: -3.  
Weather flag and details are stored on every pick and returned in the API response.

#### 4. Agent Weight Optimisation
`fit_agent_weights()` runs logistic regression on the full agent signal matrix vs outcomes.  
Kicks in at 300+ resolved picks. Refits every 50 new picks.  
`get_agent_weights()` returns fitted coefficients if available, else hand-tuned defaults.  
`build_consensus_pick()` uses these weights in the confidence formula — it self-improves over time.  
`GET /api/model-weights` shows current weights, per-agent win rates, and calibration status.

---

## Deploy to Render

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "algobets backend v4"
gh repo create algobets-backend --private --push
```

### 2. Create Render Web Service
1. [render.com](https://render.com) → New → Web Service
2. Connect GitHub repo
3. Render auto-detects `render.yaml` → Deploy
4. Persistent disk (`/data`) created automatically

### 3. Environment variables (Render dashboard → Environment tab)

| Key | Source |
|-----|--------|
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com) |
| `STRIPE_SECRET_KEY` | Stripe Dashboard → API Keys |
| `STRIPE_WEBHOOK_SECRET` | Stripe Dashboard → Webhooks |
| `STRIPE_PRO_PRICE_IDS` | Stripe Dashboard → Products |
| `STRIPE_SHARP_PRICE_IDS` | Stripe Dashboard → Products |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) — optional |
| `BACKEND_API_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `FRONTEND_URL` | `https://algobets.ai` |

`DATA_DIR`, `CACHE_DIR`, `ODDS_BOOKMAKERS` are pre-set in `render.yaml`.

### 4. Stripe webhook
Stripe Dashboard → Webhooks → Add endpoint:
- URL: `https://your-render-url.onrender.com/api/stripe-webhook`
- Events: `checkout.session.completed`, `customer.subscription.deleted`, `customer.subscription.updated`

### 5. Update frontend
```js
const BACKEND = "https://your-actual-render-url.onrender.com";
// Every request must include:
headers: { "X-API-Key": YOUR_BACKEND_API_KEY }
```

---

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
uvicorn main:app --reload --port 8000

# Test
curl http://localhost:8000/health
curl http://localhost:8000/scan -H "X-API-Key: your_key"
curl http://localhost:8000/api/model-weights
curl http://localhost:8000/api/performance
```

---

## API endpoints

| Method | Path | Auth | Description | Source |
|--------|------|------|-------------|--------|
| GET | `/health` | — | Health check | — |
| GET | `/api/quota` | — | Credit status + cache info | — |
| GET | `/scan` | ✓ | 7-agent picks (weather-adjusted, calibrated) | ESPN+AN+Pinnacle+Weather |
| GET | `/api/line-movement` | ✓ | Steam moves & sharp action | Action Network+ESPN |
| GET | `/api/ev-finder` | ✓ | Positive EV (multi-book) | **Odds API** |
| GET | `/api/arb-detect` | ✓ | Arbitrage opportunities | **Odds API** |
| GET | `/api/player-props` | ✓ | Player prop analysis | ESPN |
| GET | `/api/injuries` | — | Live injury feed | ESPN |
| GET | `/api/performance` | — | Win%, ROI, CLV, agent accuracy | SQLite |
| GET | `/api/model-weights` | — | Current agent weights + calibration status | SQLite |
| GET | `/api/picks-history` | ✓ | Raw pick log with results | SQLite |
| POST | `/api/resolve-picks` | ✓ | Write game results (triggers auto-refit) | SQLite |
| POST | `/api/plan-status` | ✓ | Verify Stripe subscription | Stripe |
| POST | `/api/create-portal-session` | ✓ | Billing portal | Stripe |
| POST | `/api/stripe-webhook` | — | Stripe events | Stripe |
| POST | `/chat` | ✓ | AI assistant | OpenAI |

---

## ROI tracking + self-improvement loop

```
Pick surfaced by /scan
        ↓
log_pick() → picks.db (confidence_raw, agents_fired, weather_flag, pinnacle_line)
        ↓
_clv_capture_loop() — runs every 10min
  → 45min before game: fetches Pinnacle closing line → clv_pinnacle_close
        ↓
Game completes → POST /api/resolve-picks
  → writes result, pnl
  → triggers maybe_refit_models()
        ↓
At 200 picks: fit_calibration()
  → Platt scaling: raw confidence → calibrated win probability
  → stored in calibration table, loaded on startup

At 300 picks: fit_agent_weights()
  → logistic regression on agent signal vectors vs outcomes
  → optimal weights stored in agent_stats.fitted_weight
  → build_consensus_pick() uses them automatically
        ↓
Model gets better with every 50 new resolved picks
```

---

## Cost estimate (monthly)

| Item | Cost |
|------|------|
| Odds API (Developer) | $79/mo |
| Render Web Service (Starter) | $7/mo |
| Render Persistent Disk 1GB | $0.25/mo |
| OpenAI gpt-4o-mini (chat) | ~$1–5/mo |
| ESPN, Action Network, Pinnacle, Open-Meteo | **$0** |
| **Total** | **~$87–91/mo** |

---

## New DB columns (v3 → v4 migration)

`init_db()` runs `ALTER TABLE ... ADD COLUMN` for new columns on startup — safe for existing v3 databases. No manual migration needed.

New columns in `picks`:
- `confidence_raw` — pre-calibration score
- `confidence_calibrated` — Platt-scaled probability
- `clv_pinnacle_close` — Pinnacle line captured 30min before game
- `clv_captured_at` — timestamp of close capture
- `weather_flag` — null | 'wind' | 'cold' | 'precip' | combinations
- `weather_details` — JSON weather snapshot

New table: `calibration` — stores Platt scaling params history  
New column in `agent_stats`: `fitted_weight` — logistic regression coefficient
