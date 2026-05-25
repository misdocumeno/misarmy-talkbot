"""Pytest bootstrap: supported locale + reset app singletons between tests."""

import os

# Must run before any misarmy_talkbot import (translations exits on LANG=C).
if os.environ.get('LANG', 'C').split('.')[0] not in (
    'en_US',
    'es_ES',
    'es_AR',
):
    os.environ['LANG'] = 'en_US.UTF-8'

from collections.abc import Generator

import pytest

from tests.helpers import reset_singletons


@pytest.fixture(autouse=True)
def _isolate_singletons() -> Generator[None, None, None]:
    reset_singletons()
    yield
    reset_singletons()
