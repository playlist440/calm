"""Fixtures for the control core. The helpers themselves live in helpers.py."""

from __future__ import annotations

import pytest

from custom_components.calm.core import DimProfile

from .helpers import make_profile


@pytest.fixture
def profile() -> DimProfile:
    return make_profile()
