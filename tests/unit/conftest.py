import pytest

from tests.unit.factories import FULL_ENV


@pytest.fixture
def env():
    return dict(FULL_ENV)
