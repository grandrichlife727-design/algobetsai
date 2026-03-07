# Paid Ads Testing Matrix (Launch)

## Monthly Budget (Starter)
- Total: $900/month
- Meta (IG/FB): $500
- TikTok: $250
- X: $150

## Campaign Structure
- Objective: traffic -> signup -> checkout
- 3 campaigns per channel:
  - C1: Free tier hook
  - C2: Premium value hook
  - C3: VIP speed/timing hook

## Creative Test Grid
- 3 hooks x 2 visuals x 2 CTAs = 12 ads/channel
- Hooks:
  - "Stop looking for winners. Start looking for edges."
  - "If your app can’t compare books, you’re guessing."
  - "Timing matters: edge dies fast."
- Visuals:
  - Phone UI demo
  - Face-to-camera explainer
- CTAs:
  - "Try Free"
  - "Open App"

## KPI Targets (First 14 Days)
- CTR: >= 1.2%
- CPC: <= $1.50
- Landing->Signup CVR: >= 8%
- Signup->Checkout Start: >= 10%
- Checkout Start->Paid: >= 20%
- Target blended CAC:
  - Premium <= $45
  - VIP <= $80

## Decision Rules
- Kill after 1,500 impressions if CTR < 0.8%
- Kill after $20 spend if CPC > $2.25 and no signup
- Scale +30% budget every 48h if:
  - CTR >= 1.5%
  - CPC <= $1.20
  - Signup CVR >= 10%
- Pause all creative with negative comments about "guaranteed wins" language

## Funnel Events To Track
- `app_open`
- `scan_manual`
- `auth_completed`
- `checkout_started`
- `checkout_redirected`
- `checkout_success`

## Weekly Optimization Workflow
1. Pull top 5 ads by signup CPA.
2. Duplicate winners with 1 new hook each.
3. Refresh first 2 seconds of video for losers before relaunch.
4. Reallocate 20% budget from worst channel to best channel.

## Compliance Guardrails
- Do not claim guaranteed outcomes.
- Use: "data-driven", "edge", "process", "long-term EV".
- Include 21+ responsible gaming line in ad copy/footer where allowed.
