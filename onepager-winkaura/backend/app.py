import json
import os
import time
from pathlib import Path
from typing import Any, Dict

import httpx
import stripe
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="WinkAura Order Bridge", version="1.0.0")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "").strip()
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN", "").strip()
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10").strip() or "2024-10"

# Must be the exact Shopify variant ID that maps to your CJ-linked product variant.
SHOPIFY_VARIANT_ID = os.getenv("SHOPIFY_VARIANT_ID", "").strip()
WINKAURA_SKU = os.getenv("WINKAURA_SKU", "CJJJ110044801AZ").strip() or "CJJJ110044801AZ"

DEFAULT_UNIT_PRICE_USD = float(os.getenv("DEFAULT_UNIT_PRICE_USD", "29") or 29)
ORDER_TAGS = os.getenv("ORDER_TAGS", "winkaura,stripe,cj-fulfillment").strip() or "winkaura,stripe,cj-fulfillment"

DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/winkaura_bridge"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
EVENTS_FILE = DATA_DIR / "processed_events.json"

stripe.api_key = STRIPE_SECRET_KEY


def _load_processed_events() -> Dict[str, int]:
    if not EVENTS_FILE.exists():
        return {}
    try:
        return json.loads(EVENTS_FILE.read_text("utf-8"))
    except Exception:
        return {}


def _save_processed_events(events: Dict[str, int]) -> None:
    # Keep file small.
    now = int(time.time())
    cutoff = now - (7 * 24 * 3600)
    pruned = {k: v for k, v in events.items() if int(v) >= cutoff}
    EVENTS_FILE.write_text(json.dumps(pruned), encoding="utf-8")


def _processed(event_id: str) -> bool:
    events = _load_processed_events()
    return event_id in events


def _mark_processed(event_id: str) -> None:
    events = _load_processed_events()
    events[event_id] = int(time.time())
    _save_processed_events(events)


def _bundle_qty_from_session(session: Any) -> int:
    md = getattr(session, "metadata", {}) or {}
    raw = str(md.get("bundle_qty") or md.get("bundle") or "").strip()
    if raw.isdigit():
        q = int(raw)
        return max(1, min(10, q))

    # Fallback: sum Stripe line item quantities.
    qty_total = 0
    try:
        line_items = stripe.checkout.Session.list_line_items(session.id, limit=20)
        for li in (line_items.data or []):
            qty_total += int(getattr(li, "quantity", 0) or 0)
    except Exception:
        qty_total = 0

    if qty_total > 0:
        return max(1, min(10, qty_total))

    return 1


def _build_draft_order_payload(session: Any, qty: int) -> Dict[str, Any]:
    customer_email = None
    customer_name = None

    details = getattr(session, "customer_details", None)
    if details:
        customer_email = getattr(details, "email", None)
        customer_name = getattr(details, "name", None)

    if not customer_email:
        customer_email = getattr(session, "customer_email", None)

    if not customer_email:
        raise HTTPException(status_code=400, detail="Stripe session missing customer email")

    if not SHOPIFY_VARIANT_ID:
        raise HTTPException(status_code=500, detail="SHOPIFY_VARIANT_ID not configured")

    note_lines = [
        f"source: stripe",
        f"stripe_session_id: {session.id}",
        f"bundle_qty: {qty}",
        f"supplier_sku: {WINKAURA_SKU}",
    ]

    draft: Dict[str, Any] = {
        "draft_order": {
            "email": customer_email,
            "line_items": [
                {
                    "variant_id": int(SHOPIFY_VARIANT_ID),
                    "quantity": int(qty),
                }
            ],
            "tags": ORDER_TAGS,
            "note": "\n".join(note_lines),
            "note_attributes": [
                {"name": "stripe_session_id", "value": str(session.id)},
                {"name": "bundle_qty", "value": str(qty)},
                {"name": "supplier_sku", "value": WINKAURA_SKU},
            ],
        }
    }

    if customer_name:
        draft["draft_order"]["shipping_address"] = {"name": customer_name}

    return draft


async def _create_shopify_draft_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="Shopify credentials are not configured")

    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/draft_orders.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=payload)

    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Shopify draft order failed: {resp.status_code} {resp.text[:500]}")

    body = resp.json()
    return body.get("draft_order", body)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "stripe_key": bool(STRIPE_SECRET_KEY),
        "stripe_webhook_secret": bool(STRIPE_WEBHOOK_SECRET),
        "shopify_domain": bool(SHOPIFY_STORE_DOMAIN),
        "shopify_token": bool(SHOPIFY_ADMIN_TOKEN),
        "shopify_variant_id": bool(SHOPIFY_VARIANT_ID),
    }


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET missing")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook parse error: {str(exc)}")

    event_id = str(event.get("id") or "")
    if event_id and _processed(event_id):
        return JSONResponse({"ok": True, "duplicate": True})

    event_type = event.get("type")
    if event_type != "checkout.session.completed":
        if event_id:
            _mark_processed(event_id)
        return JSONResponse({"ok": True, "ignored": event_type})

    session_obj = event.get("data", {}).get("object", {})
    session_id = str(session_obj.get("id") or "")
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing checkout session id")

    # Retrieve full session from Stripe for reliable fields and metadata.
    session = stripe.checkout.Session.retrieve(session_id)

    if str(getattr(session, "payment_status", "")) != "paid":
        # If unpaid, ignore and do not create fulfillment order.
        if event_id:
            _mark_processed(event_id)
        return JSONResponse({"ok": True, "ignored": "payment_not_paid"})

    qty = _bundle_qty_from_session(session)
    draft_payload = _build_draft_order_payload(session, qty)
    draft_order = await _create_shopify_draft_order(draft_payload)

    if event_id:
        _mark_processed(event_id)

    return JSONResponse(
        {
            "ok": True,
            "event": event_type,
            "stripe_session_id": session_id,
            "qty": qty,
            "shopify_draft_order_id": draft_order.get("id"),
            "shopify_draft_order_name": draft_order.get("name"),
            "shopify_invoice_url": draft_order.get("invoice_url"),
        }
    )
