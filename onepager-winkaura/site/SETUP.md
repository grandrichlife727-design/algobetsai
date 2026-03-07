# WinkAura One-Page Setup

## Product + source
- Product: Magnetic Eyelash Liquid Eyeliner Set
- Supplier: CJdropshipping
- SKU: CJJJ110044801AZ
- Last seen source cost: ~$2.93

## Stripe setup
In `index.html`, replace these placeholders:
- `REPLACE_SINGLE`
- `REPLACE_DOUBLE`
- `REPLACE_TRIPLE`

Suggested prices:
- 1x: $29
- 2x: $49
- 3x: $69

## Stripe -> Shopify inventory automation
Use backend service in:
- `../backend/app.py`

Stripe webhook endpoint:
- `https://YOUR_BACKEND_DOMAIN/webhook/stripe`

Stripe event to send:
- `checkout.session.completed`

Add metadata on each Stripe payment link:
- Single link: `bundle_qty=1`
- Double link: `bundle_qty=2`
- Triple link: `bundle_qty=3`

Backend required env vars:
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `SHOPIFY_STORE_DOMAIN`
- `SHOPIFY_ADMIN_TOKEN`
- `SHOPIFY_VARIANT_ID` (must match your CJ-linked Shopify variant)
- `WINKAURA_SKU=CJJJ110044801AZ`

## Local preview
```bash
cd /Users/fortunefavors/Documents/GitHub/algobetsai/onepager-winkaura
python3 -m http.server 8082
```
Open:
- http://127.0.0.1:8082/site/index.html
