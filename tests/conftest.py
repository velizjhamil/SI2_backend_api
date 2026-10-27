"""Validate the isolated test database before test modules can connect to it."""

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pytest_guard import UnsafeTestEnvironment, validate_environment  # noqa: E402


def pytest_configure(config):
    try:
        validate_environment()
    except UnsafeTestEnvironment as exc:
        raise pytest.UsageError(f"Unsafe test database configuration: {exc}") from exc
