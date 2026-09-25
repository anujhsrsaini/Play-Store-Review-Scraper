"""Stripe self-serve billing integration for Review Lens.

Supports:
- Subscriptions: Starter ($19/mo) and Agency ($49/mo)
- One-time credit pack: Indie Pass ($15 once for 20 deep analyses)
- Stripe Webhook handling for automated tier upgrades and cancellations
- Dev/mock fallback when STRIPE_SECRET_KEY is unset so local testing works out of the box
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

from sqlalchemy.orm import sessionmaker

from .config import Settings
from .db import User

logger = logging.getLogger(__name__)

PLAN_PRICES = {
    "starter": {
        "name": "Indie Pro ($19/mo)",
        "mode": "subscription",
        "credits": 0,
        "tier": "starter",
    },
    "pro": {"name": "Agency Pro ($49/mo)", "mode": "subscription", "credits": 0, "tier": "pro"},
    "pass": {"name": "Indie Pass ($15 once)", "mode": "payment", "credits": 20, "tier": "free"},
}


def create_checkout_session(
    user_id: str,
    email: str,
    plan: str,
    settings: Settings,
    base_url: str = "http://localhost:8000",
) -> str:
    """Create a Stripe checkout session URL, or a local dev simulation URL when unconfigured."""
    plan_info = PLAN_PRICES.get(plan, PLAN_PRICES["starter"])
    success_url = urljoin(base_url, "/?checkout=success&plan=" + plan)
    cancel_url = urljoin(base_url, "/?checkout=cancelled")

    if not settings.stripe_secret_key:
        logger.info(
            "STRIPE_SECRET_KEY unset; providing local mock checkout URL for plan '%s'", plan
        )
        return urljoin(base_url, f"/api/billing/mock-activate?plan={plan}&user_id={user_id}")

    import stripe

    stripe.api_key = settings.stripe_secret_key

    # Resolve price id
    price_id = ""
    if plan == "starter":
        price_id = settings.stripe_price_starter
    elif plan == "pass":
        price_id = settings.stripe_price_pass

    line_items: list[dict[str, Any]]
    if price_id:
        line_items = [{"price": price_id, "quantity": 1}]
    else:
        # Dynamic line item fallback if explicit price IDs aren't pre-configured in Stripe dashboard
        unit_amount = 1900 if plan == "starter" else (4900 if plan == "pro" else 1500)
        currency = "usd"
        recurring = {"interval": "month"} if plan_info["mode"] == "subscription" else None
        price_data: dict[str, Any] = {
            "currency": currency,
            "product_data": {"name": f"Review Lens {plan_info['name']}"},
            "unit_amount": unit_amount,
        }
        if recurring:
            price_data["recurring"] = recurring
        line_items = [{"price_data": price_data, "quantity": 1}]

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=line_items,
        mode=plan_info["mode"],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=user_id,
        customer_email=email if email else None,
        metadata={"plan": plan, "user_id": user_id},
    )
    return session.url or success_url


def handle_webhook_event(
    payload: bytes,
    sig_header: str | None,
    session_factory: sessionmaker,
    settings: Settings,
) -> dict[str, Any]:
    """Process incoming Stripe webhook events."""
    event_data: dict[str, Any]
    event_type: str = ""

    if settings.stripe_webhook_secret and sig_header:
        import stripe

        stripe.api_key = settings.stripe_secret_key
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.stripe_webhook_secret
            )
            event_data = event["data"]["object"]
            event_type = event["type"]
        except Exception as exc:
            logger.warning("Stripe webhook verification failed: %s", exc)
            raise ValueError(f"Webhook verification failed: {exc}") from exc
    else:
        try:
            raw = json.loads(payload.decode("utf-8"))
            event_data = raw.get("data", {}).get("object", {})
            event_type = raw.get("type", "")
        except Exception as exc:
            logger.warning("Failed to parse unverified webhook json: %s", exc)
            raise ValueError("Malformed JSON payload") from exc

    logger.info("Processing Stripe webhook event: %s", event_type)

    if event_type == "checkout.session.completed":
        user_id = event_data.get("client_reference_id") or event_data.get("metadata", {}).get(
            "user_id"
        )
        plan = event_data.get("metadata", {}).get("plan", "starter")
        mode = event_data.get("mode")
        customer_id = event_data.get("customer")

        if user_id:
            with session_factory() as session:
                user = session.get(User, user_id)
                if user is not None:
                    if customer_id:
                        user.stripe_customer_id = str(customer_id)
                    if mode == "subscription":
                        user.tier = "pro" if plan == "pro" else "starter"
                        user.subscription_status = "active"
                    elif mode == "payment" or plan == "pass":
                        user.extra_credits += 20
                    session.commit()
                    logger.info(
                        "Upgraded user %s to tier=%s (credits=%d)",
                        user.id,
                        user.tier,
                        user.extra_credits,
                    )
                    return {"status": "success", "user_id": user.id, "tier": user.tier}

    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = event_data.get("customer")
        if customer_id:
            with session_factory() as session:
                from sqlalchemy import select

                user = session.execute(
                    select(User).where(User.stripe_customer_id == str(customer_id))
                ).scalar_one_or_none()
                if user is not None:
                    user.tier = "free"
                    user.subscription_status = "canceled"
                    session.commit()
                    logger.info(
                        "Downgraded customer %s (user %s) to free tier", customer_id, user.id
                    )
                    return {"status": "canceled", "user_id": user.id}

    return {"status": "ignored", "type": event_type}
