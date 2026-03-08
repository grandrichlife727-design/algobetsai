# LumaLift One-Page Setup

## Files
- `site/index.html` -> complete landing page
- `../branding/logo-mark.svg` -> logo icon used in header

## Stripe wiring
In `site/index.html`, update this block:

```js
const STRIPE_LINKS = {
  single: "https://buy.stripe.com/REPLACE_SINGLE",
  double: "https://buy.stripe.com/REPLACE_DOUBLE",
  triple: "https://buy.stripe.com/REPLACE_TRIPLE"
};
```

Replace each URL with your real Stripe Payment Link URL.

## Suggested Stripe products
- LumaLift 1x = $49
- LumaLift 2x = $79
- LumaLift 3x = $99

## Publish options
1. GitHub Pages (fast)
2. Netlify drag/drop
3. Vercel static deploy
4. Shopify custom page embed

## Quick local preview
From repo root:

```bash
cd /Users/fortunefavors/Documents/GitHub/algobetsai/onepager-lumalift
python3 -m http.server 8081
```

Then open:
- `http://127.0.0.1:8081/site/index.html`
