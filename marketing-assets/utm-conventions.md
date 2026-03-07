# UTM Naming Conventions

## Standard Format
- `utm_source`: platform
- `utm_medium`: paid_social | organic_social | influencer | email
- `utm_campaign`: yyyy_mm_offer_objective
- `utm_content`: creative hook + variant
- `utm_term`: audience or interest cluster

## Allowed Values
- `utm_source`:
  - `instagram`
  - `facebook`
  - `tiktok`
  - `x`
  - `youtube`
- `utm_medium`:
  - `paid_social`
  - `organic_social`
  - `influencer`
- `utm_campaign` examples:
  - `2026_03_launch_free_signup`
  - `2026_03_launch_premium_checkout`
  - `2026_03_launch_vip_checkout`
- `utm_content` examples:
  - `hook_ev_v1`
  - `hook_clv_v2`
  - `hook_timing_v1`
- `utm_term` examples:
  - `nba_interest`
  - `sportsbetting_broad`
  - `lookalike_paid_users`

## Example URL
`https://grandrichlife727-design.github.io/algobetsai/landing-improved.html?utm_source=instagram&utm_medium=paid_social&utm_campaign=2026_03_launch_free_signup&utm_content=hook_ev_v1&utm_term=sportsbetting_broad`

## Rules
- Use lowercase only.
- Use underscores, no spaces.
- Don’t reuse `utm_content` IDs across different creative.
- Keep one source of truth in this file before launching new ad sets.
