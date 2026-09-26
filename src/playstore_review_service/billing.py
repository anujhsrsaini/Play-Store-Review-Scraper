"""Self-serve billing integration for Review Lens.

Dodo Payments only (Merchant of Record - India individual PAN support for global
USD sales), with a local mock checkout when keys are unset.
- Subscriptions: Starter ($19/mo) and Agency Pro ($49/mo)
- One-time credit pack: Indie Pass ($15 once for 20 deep analyses)
- Standard Webhooks signature verification and automated tier upgrades/cancellations
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
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
        "default_dodo_product_id": "pdt_0NoMFta1GvOnnuvqQupPi",
    },
    "pro": {
        "name": "Agency Pro ($49/mo)",
        "mode": "subscription",
        "credits": 0,
        "tier": "pro",
        "default_dodo_product_id": "",
    },
    "pass": {
        "name": "Indie Pass ($15 once)",
        "mode": "payment",
        "credits": 20,
        "tier": "free",
        "default_dodo_product_id": "pdt_0NoMFtbyuC8aLbMY6ztiD",
    },
}


def create_checkout_session(
    user_id: str,
    email: str,
    plan: str,
    settings: Settings,
    base_url: str = "http://localhost:8000",
) -> str:
    """Create a checkout session URL via Dodo Payments, with mock fallback."""
    plan_info = PLAN_PRICES.get(plan, PLAN_PRICES["starter"])
    success_url = urljoin(base_url, "/?checkout=success&plan=" + plan)

    # 1. Primary: Dodo Payments (if configured)
    if settings.dodo_payments_api_key:
        try:
            import dodopayments

            client = dodopayments.DodoPayments(
                bearer_token=settings.dodo_payments_api_key,
                environment=settings.dodo_payments_environment,
            )

            product_id = ""
            if plan == "starter":
                product_id = (
                    settings.dodo_product_id_starter or plan_info["default_dodo_product_id"]
                )
            elif plan == "pass":
                product_id = settings.dodo_product_id_pass or plan_info["default_dodo_product_id"]
            elif plan == "pro":
                product_id = settings.dodo_product_id_pro or plan_info["default_dodo_product_id"]

            if not product_id:
                for prod in client.products.list():
                    p_name = getattr(prod, "name", "").lower()
                    if plan == "starter" and "pro" in p_name:
                        product_id = prod.product_id
                        break
                    elif plan == "pass" and "pass" in p_name:
                        product_id = prod.product_id
                        break

            customer_param = None
            if email:
                customer_param = {
                    "email": email,
                    "name": email.split("@")[0],
                }

            session = client.checkout_sessions.create(
                product_cart=[{"product_id": product_id, "quantity": 1}],
                customer=customer_param,
                return_url=success_url,
                metadata={"user_id": user_id, "plan": plan},
            )
            logger.info(
                "Created Dodo Payments checkout session %s for user %s",
                session.session_id,
                user_id,
            )
            return session.checkout_url
        except Exception as exc:
            logger.error("Failed to create Dodo Payments checkout session: %s", exc)
            return urljoin(
                base_url,
                f"/api/billing/mock-activate?plan={plan}&user_id={user_id}",
            )

    # 2. Local Mock Checkout
    logger.info(
        "No payment provider configured; providing local mock checkout URL for plan '%s'",
        plan,
    )
    return urljoin(
        base_url,
        f"/api/billing/mock-activate?plan={plan}&user_id={user_id}",
    )


def handle_webhook_event(
    payload: bytes,
    sig_header: str | None,
    session_factory: sessionmaker,
    settings: Settings,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Process incoming Dodo Payments webhook events."""
    headers_dict = dict(headers or {})
    event_data: dict[str, Any] = {}
    event_type: str = ""

    try:
        raw_json = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse webhook json: %s", exc)
        raise ValueError("Malformed JSON payload") from exc

    has_dodo_sig = bool(headers_dict.get("webhook-signature") or sig_header)
    if settings.dodo_payments_webhook_secret and has_dodo_sig:
        import dodopayments

        client = dodopayments.DodoPayments(
            bearer_token=settings.dodo_payments_api_key,
            webhook_key=settings.dodo_payments_webhook_secret,
            environment=settings.dodo_payments_environment,
        )
        verify_headers = {
            "webhook-id": headers_dict.get("webhook-id", ""),
            "webhook-signature": headers_dict.get("webhook-signature") or sig_header or "",
            "webhook-timestamp": headers_dict.get("webhook-timestamp", ""),
        }
        try:
            unwrapped = client.webhooks.unwrap(
                payload.decode("utf-8"),
                headers=verify_headers,
            )
            event_type = unwrapped.type
            event_data = (
                unwrapped.data.model_dump()
                if hasattr(unwrapped.data, "model_dump")
                else (unwrapped.data if isinstance(unwrapped.data, dict) else {})
            )
        except Exception as exc:
            logger.warning("Dodo webhook signature verification failed: %s", exc)
            raise ValueError(f"Dodo webhook verification failed: {exc}") from exc
    else:
        event_type = raw_json.get("type", "")
        event_data = raw_json.get("data", {})

    logger.info("Processing Dodo Payments webhook event: %s", event_type)

    if event_type == "payment.succeeded":
        metadata = event_data.get("metadata") or {}
        user_id = metadata.get("user_id")
        plan = metadata.get("plan", "starter")
        customer_dict = event_data.get("customer") or {}
        customer_id = customer_dict.get("customer_id")

        if user_id:
            with session_factory() as session:
                user = session.get(User, user_id)
                if user is not None:
                    if customer_id:
                        user.dodo_customer_id = str(customer_id)
                    if plan == "pass":
                        user.extra_credits += 20
                    else:
                        user.tier = "pro" if plan == "pro" else "starter"
                        user.subscription_status = "active"
                    session.commit()
                    logger.info(
                        "Upgraded user %s via Dodo payment (tier=%s, credits=%d)",
                        user.id,
                        user.tier,
                        user.extra_credits,
                    )
                    return {
                        "status": "success",
                        "user_id": user.id,
                        "tier": user.tier,
                        "credits": user.extra_credits,
                    }

    elif event_type in ("subscription.active", "subscription.renewed"):
        metadata = event_data.get("metadata") or {}
        user_id = metadata.get("user_id")
        plan = metadata.get("plan", "starter")
        customer_dict = event_data.get("customer") or {}
        customer_id = customer_dict.get("customer_id")

        if user_id:
            with session_factory() as session:
                user = session.get(User, user_id)
                if user is not None:
                    if customer_id:
                        user.dodo_customer_id = str(customer_id)
                    user.tier = "pro" if plan == "pro" else "starter"
                    user.subscription_status = "active"
                    session.commit()
                    logger.info(
                        "Activated Dodo subscription for user %s (tier=%s)",
                        user.id,
                        user.tier,
                    )
                    return {"status": "success", "user_id": user.id, "tier": user.tier}

    elif event_type in (
        "subscription.cancelled",
        "subscription.expired",
        "subscription.failed",
        "subscription.paused",
    ):
        metadata = event_data.get("metadata") or {}
        user_id = metadata.get("user_id")
        customer_dict = event_data.get("customer") or {}
        customer_id = customer_dict.get("customer_id")

        with session_factory() as session:
            from sqlalchemy import select

            user = None
            if user_id:
                user = session.get(User, user_id)
            elif customer_id:
                user = session.execute(
                    select(User).where(User.dodo_customer_id == str(customer_id))
                ).scalar_one_or_none()

            if user is not None:
                user.tier = "free"
                user.subscription_status = "canceled"
                session.commit()
                logger.info(
                    "Downgraded user %s to free tier after Dodo event %s",
                    user.id,
                    event_type,
                )
                return {"status": "canceled", "user_id": user.id}

    return {"status": "ignored", "type": event_type}
