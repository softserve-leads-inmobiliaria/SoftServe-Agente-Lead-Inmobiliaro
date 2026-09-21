import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from db.testing import temporary_database

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def database_url():
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="module")
def engine(database_url):
    engine = create_engine(database_url)
    yield engine
    engine.dispose()
