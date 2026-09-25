"""Tests for Stripe billing integration and paid tier enforcement."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from playstore_review_service import config as config_mod
from playstore_review_service import webapp as webapp_mod
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


def test_billing_checkout_redirect_to_mock_when_no_stripe_key(billing_service):
    client, session_factory = billing_service
    res = client.get("/api/billing/checkout?plan=starter")
    assert res.status_code == 307
    assert "/api/billing/mock-activate?plan=starter" in res.headers["location"]


def test_billing_webhook_checkout_completed(billing_service):
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
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    with session_factory() as session:
        user = session.get(User, "local")
        assert user.tier == "starter"
        assert user.subscription_status == "active"
        assert user.stripe_customer_id == "cus_test123"


def test_billing_webhook_subscription_deleted(billing_service):
    client, session_factory = billing_service
    with session_factory() as session:
        user = User(
            id="user_sub",
            email="sub@test.com",
            tier="starter",
            stripe_customer_id="cus_sub_456",
            subscription_status="active",
        )
        session.add(user)
        session.commit()

    payload = {
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "customer": "cus_sub_456",
            }
        },
    }

    res = client.post(
        "/api/billing/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "canceled"

    with session_factory() as session:
        user = session.get(User, "user_sub")
        assert user.tier == "free"
        assert user.subscription_status == "canceled"
