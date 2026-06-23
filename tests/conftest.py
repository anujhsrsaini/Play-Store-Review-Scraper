"""Shared test fixtures: loaders for the recorded-shape JSON fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def app_raw() -> dict:
    return _load("app.json")


@pytest.fixture
def search_raw() -> list[dict]:
    return _load("search.json")


@pytest.fixture
def reviews_page1() -> list[dict]:
    return _load("reviews_page1.json")


@pytest.fixture
def reviews_page2() -> list[dict]:
    return _load("reviews_page2.json")
