# VIP Welcome Email Setup

This project now sends a transactional "VIP Welcome Email" immediately after Stripe webhook events upgrade a user to VIP.

## Required env vars (backend)

- `SENDGRID_API_KEY`: SendGrid API key with Mail Send permission
- `SENDGRID_FROM_EMAIL`: verified sender in SendGrid
- `STRIPE_WEBHOOK_SECRET`: Stripe webhook signing secret

## Recommended env vars (backend)

- `SENDGRID_FROM_NAME=AlgoBets Ai`
- `VIP_WELCOME_EMAIL_ENABLED=true`
- `VIP_WELCOME_SESSION_TOKEN_TTL_SECONDS=604800`
- `BACKEND_PUBLIC_BASE_URL=https://algobetsai.onrender.com`
- `FRONTEND_URL=https://grandrichlife727-design.github.io/algobetsai`
- `VIP_DISCORD_URL=<your_discord_invite_url>`

## Stripe webhook events to enable

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Endpoint:

- `POST https://algobetsai.onrender.com/api/billing/webhook`

## What gets sent

The VIP welcome email includes:

- `Open VIP Execution Tab` (session-scoped deep link)
- `Open VIP Discord Access Tab` (session-scoped deep link)
- `Open Discord Invite` (backend-gated VIP invite redirect)

## Behavior notes

- Welcome email only sends on a transition into VIP (prevents repeated sends).
- If SendGrid env vars are missing, webhook still succeeds and skips email.
- Deep-link token is a signed backend JWT and expires automatically.
