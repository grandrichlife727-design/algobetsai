# WinkAura Stripe -> Shopify Order Bridge

This service takes paid Stripe checkout events and creates Shopify draft orders using the WinkAura variant ID (for CJ-linked fulfillment flow).

## Flow
1. Customer clicks bundle button on one-page site.
2. Stripe Payment Link checkout completes.
3. Stripe sends webhook to `/webhook/stripe`.
4. Service verifies signature, loads session, determines bundle quantity.
5. Service creates Shopify Draft Order with `SHOPIFY_VARIANT_ID` and quantity.
6. Your Shopify/CJ integration handles downstream fulfillment.

## Important requirement
`SHOPIFY_VARIANT_ID` must be the variant linked to your CJ source product. If this is wrong, inventory sync and fulfillment will break.

## Endpoints
- `GET /health`
- `POST /webhook/stripe`

## Local run
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export $(grep -v '^#' .env | xargs)
uvicorn app:app --reload --port 8091
```

## Stripe webhook setup
In Stripe Developers -> Webhooks:
- Endpoint URL: `https://YOUR_BACKEND_DOMAIN/webhook/stripe`
- Events:
  - `checkout.session.completed`

Copy webhook signing secret to `STRIPE_WEBHOOK_SECRET`.

## Bundle quantity logic
Order quantity is read in this order:
1. `session.metadata.bundle_qty`
2. `session.metadata.bundle`
3. Sum of Stripe line item quantities
4. Fallback: `1`

## Payment links recommendation
Set metadata on each Stripe Payment Link / Product:
- Single: `bundle_qty=1`
- Double: `bundle_qty=2`
- Triple: `bundle_qty=3`

## Deploy on Render
Use a web service with start command:
```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Set env vars from `.env.example`.
