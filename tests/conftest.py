"""Fixtures shared across all tests (no DB dependency)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="function")
def unauthenticated_headers():
    return {}


@pytest.fixture(scope="function")
def invalid_token_headers():
    return {"Authorization": "Bearer invalid.token.here"}
