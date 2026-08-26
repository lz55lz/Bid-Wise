"""Test suite defaults that keep external-system checks explicit."""

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration checks unless a dedicated migrated test DB is selected."""
    if os.getenv("TEST_DATABASE_URL"):
        return
    marker = pytest.mark.skip(
        reason="integration test requires TEST_DATABASE_URL and a migrated PostgreSQL database"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(marker)
