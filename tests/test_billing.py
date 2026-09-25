"""Tests for Dodo Payments and Stripe billing integration and paid tier enforcement."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc  # noqa: UP017

import pytest
from fastapi.testclient import TestClient
from standardwebhooks import Webhook

from playstore_review_service import config as config_mod
from playstore_review_service import webapp as webapp_mod
from playstore_review_service.billing import create_checkout_session
from playstore_review_service.db import User, init_db, make_engine, make_session_factory


@pytest.fixture
def billing_service(tmp_path, monkeypatch):
    db_path = tmp_path / "billing_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("DEV_INPROCESS_WORKER", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("PER_USER_DAILY_ANALYSES", "5")
    monkeypatch.setenv("DODO_PAYMENTS_API_KEY", "")
    monkeypatch.setenv("DODO_PAYMENTS_WEBHOOK_SECRET", "")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
    config_mod.get_settings.cache_clear()

    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine)
    session_factory = make_session_factory(engine)

    app = webapp_mod.create_app()
    client = TestClient(app, follow_redirects=False)
    return client, session_factory


def test_api_me_initial_free_tier(billing_service):
    client, session_factory = billing_service
    res = client.get("/api/me")
    assert res.status_code == 200
    data = res.json()
    assert data["tier"] == "free"
    assert data["is_paid"] is False
    assert data["quota"] == 5
    assert data["extra_credits"] == 0


def test_billing_mock_activate_starter(billing_service):
    client, session_factory = billing_service
    res = client.get("/api/billing/mock-activate?plan=starter&user_id=local")
    assert res.status_code == 307
    assert "checkout=success&plan=starter" in res.headers["location"]

    res_me = client.get("/api/me")
    data = res_me.json()
    assert data["tier"] == "starter"
    assert data["is_paid"] is True
    assert data["quota"] == 50


def test_billing_mock_activate_indie_pass(billing_service):
    client, session_factory = billing_service
    res = client.get("/api/billing/mock-activate?plan=pass&user_id=local")
    assert res.status_code == 307

    res_me = client.get("/api/me")
    data = res_me.json()
    assert data["is_paid"] is True
    assert data["extra_credits"] == 20
    assert data["remaining"] >= 20


def test_billing_checkout_redirect_to_mock_when_no_keys(billing_service):
    client, session_factory = billing_service
    res = client.get("/api/billing/checkout?plan=starter")
    assert res.status_code == 307
    assert "/api/billing/mock-activate?plan=starter" in res.headers["location"]


def test_dodo_checkout_session_creation(billing_service, monkeypatch):
    monkeypatch.setenv("DODO_PAYMENTS_API_KEY", "dodo_test_mock_key")
    monkeypatch.setenv("DODO_PRODUCT_ID_STARTER", "pdt_starter_123")
    config_mod.get_settings.cache_clear()
    settings = config_mod.get_settings()

    mock_client = MagicMock()
    mock_session = MagicMock()
    mock_session.checkout_url = "https://test.checkout.dodopayments.com/session/cks_mock123"
    mock_session.session_id = "cks_mock123"
    mock_client.checkout_sessions.create.return_value = mock_session

    with patch("dodopayments.DodoPayments", return_value=mock_client):
        url = create_checkout_session(
            user_id="user_123",
            email="test@example.com",
            plan="starter",
            settings=settings,
            base_url="https://app.reviewlens.com",
        )
        assert url == "https://test.checkout.dodopayments.com/session/cks_mock123"
        mock_client.checkout_sessions.create.assert_called_once()
        call_kwargs = mock_client.checkout_sessions.create.call_args.kwargs
        assert call_kwargs["product_cart"] == [{"product_id": "pdt_starter_123", "quantity": 1}]
        assert call_kwargs["metadata"] == {"user_id": "user_123", "plan": "starter"}


def test_dodo_webhook_payment_succeeded_pass(billing_service):
    client, session_factory = billing_service
    with session_factory() as session:
        user = session.get(User, "local")
        if user is None:
            user = User(id="local", email="local@test.com", name="Local Tester")
            session.add(user)
            session.commit()

    payload = {
        "business_id": "biz_test",
        "type": "payment.succeeded",
        "data": {
            "metadata": {"user_id": "local", "plan": "pass"},
            "customer": {"customer_id": "cus_dodo_1"},
        },
    }

    res = client.post(
        "/api/billing/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "webhook-id": "msg_dodo_1"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    with session_factory() as session:
        user = session.get(User, "local")
        assert user.extra_credits == 20
        assert user.stripe_customer_id == "cus_dodo_1"


def test_dodo_webhook_subscription_active(billing_service):
    client, session_factory = billing_service
    with session_factory() as session:
        user = session.get(User, "local")
        if user is None:
            user = User(id="local", email="local@test.com", name="Local Tester")
            session.add(user)
            session.commit()

    payload = {
        "business_id": "biz_test",
        "type": "subscription.active",
        "data": {
            "subscription_id": "sub_dodo_1",
            "metadata": {"user_id": "local", "plan": "starter"},
            "customer": {"customer_id": "cus_dodo_2"},
        },
    }

    res = client.post(
        "/api/billing/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "webhook-id": "msg_dodo_2"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    with session_factory() as session:
        user = session.get(User, "local")
        assert user.tier == "starter"
        assert user.subscription_status == "active"
        assert user.stripe_customer_id == "cus_dodo_2"


def test_dodo_webhook_subscription_cancelled(billing_service):
    client, session_factory = billing_service
    with session_factory() as session:
        user = User(
            id="user_dodo_sub",
            email="dodo_sub@test.com",
            tier="starter",
            stripe_customer_id="cus_dodo_3",
            subscription_status="active",
        )
        session.add(user)
        session.commit()

    payload = {
        "business_id": "biz_test",
        "type": "subscription.cancelled",
        "data": {
            "customer": {"customer_id": "cus_dodo_3"},
            "metadata": {"user_id": "user_dodo_sub"},
        },
    }

    res = client.post(
        "/api/billing/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "webhook-id": "msg_dodo_3"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "canceled"

    with session_factory() as session:
        user = session.get(User, "user_dodo_sub")
        assert user.tier == "free"
        assert user.subscription_status == "canceled"


def test_dodo_webhook_verified_signature(billing_service, monkeypatch):
    secret = "whsec_MFwwDQYJKoZIhvcNAQEBBQADSWAwSAJBAK3"
    monkeypatch.setenv("DODO_PAYMENTS_API_KEY", "dodo_test_key")
    monkeypatch.setenv("DODO_PAYMENTS_WEBHOOK_SECRET", secret)
    config_mod.get_settings.cache_clear()

    client, session_factory = billing_service
    with session_factory() as session:
        user = User(id="user_verified", email="v@test.com")
        session.add(user)
        session.commit()

    payload = json.dumps({
        "business_id": "biz_test",
        "type": "payment.succeeded",
        "data": {
            "customer": {"customer_id": "cus_v1"},
            "metadata": {"user_id": "user_verified", "plan": "pass"},
        },
    })
    wh = Webhook(secret)
    now = datetime.now(UTC)
    sig = wh.sign("msg_test_sig", now, payload)

    res = client.post(
        "/api/billing/webhook",
        content=payload.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "webhook-id": "msg_test_sig",
            "webhook-timestamp": str(int(now.timestamp())),
            "webhook-signature": sig,
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    with session_factory() as session:
        user = session.get(User, "user_verified")
        assert user.extra_credits == 20


def test_stripe_webhook_checkout_completed(billing_service):
    client, session_factory = billing_service
    with session_factory() as session:
        user = session.get(User, "local")
        if user is None:
            user = User(id="local", email="local@test.com", name="Local Tester")
            session.add(user)
            session.commit()

    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": "local",
                "mode": "subscription",
                "customer": "cus_test123",
                "metadata": {"plan": "starter", "user_id": "local"},
            }
        },
    }

    res = client.post(
        "/api/billing/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "stripe-signature": "simulated"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    with session_factory() as session:
        user = session.get(User, "local")
        assert user.tier == "starter"
        assert user.subscription_status == "active"
        assert user.stripe_customer_id == "cus_test123"
