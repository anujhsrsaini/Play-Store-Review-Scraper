"""Self-serve billing integration for Review Lens.

Supports:
- Dodo Payments (Merchant of Record - India individual PAN support for global USD sales)
- Stripe (fallback)
- Local mock checkout when keys are unset
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
    """Create a checkout session URL via Dodo Payments or Stripe, with mock fallback."""
    plan_info = PLAN_PRICES.get(plan, PLAN_PRICES["starter"])
    success_url = urljoin(base_url, "/?checkout=success&plan=" + plan)
    cancel_url = urljoin(base_url, "/?checkout=cancelled")

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
                    settings.dodo_product_id_starter
                    or plan_info["default_dodo_product_id"]
                )
            elif plan == "pass":
                product_id = (
                    settings.dodo_product_id_pass
                    or plan_info["default_dodo_product_id"]
                )
            elif plan == "pro":
                product_id = (
                    settings.dodo_product_id_pro
                    or plan_info["default_dodo_product_id"]
                )

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
            if not settings.stripe_secret_key:
                return urljoin(
                    base_url,
                    f"/api/billing/mock-activate?plan={plan}&user_id={user_id}",
                )

    # 2. Fallback: Stripe (if configured)
    if settings.stripe_secret_key:
        import stripe

        stripe.api_key = settings.stripe_secret_key

        price_id = ""
        if plan == "starter":
            price_id = settings.stripe_price_starter
        elif plan == "pass":
            price_id = settings.stripe_price_pass

        line_items: list[dict[str, Any]]
        if price_id:
            line_items = [{"price": price_id, "quantity": 1}]
        else:
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

    # 3. Local Mock Checkout
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
    """Process incoming Dodo Payments or Stripe webhook events."""
    headers_dict = dict(headers or {})
    event_data: dict[str, Any] = {}
    event_type: str = ""

    # Detect Dodo vs Stripe
    is_dodo = bool(
        headers_dict.get("webhook-signature")
        or headers_dict.get("webhook-id")
        or (settings.dodo_payments_api_key and not sig_header)
    )

    try:
        raw_json = json.loads(payload.decode("utf-8"))
        is_dodo_prefix = raw_json.get("type", "").startswith(("payment.", "subscription."))
        if "business_id" in raw_json or is_dodo_prefix:
            is_dodo = True
        elif raw_json.get("type", "").startswith(("checkout.", "customer.")):
            is_dodo = False
    except Exception as exc:
        if not is_dodo and not sig_header:
            logger.warning("Failed to parse webhook json: %s", exc)
            raise ValueError("Malformed JSON payload") from exc
        raw_json = {}

    if is_dodo:
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
                            user.stripe_customer_id = str(customer_id)
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
                            user.stripe_customer_id = str(customer_id)
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
                        select(User).where(User.stripe_customer_id == str(customer_id))
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

    # Stripe Webhook path
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
        event_data = raw_json.get("data", {}).get("object", {})
        event_type = raw_json.get("type", "")

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
