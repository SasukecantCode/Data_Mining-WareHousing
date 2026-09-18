import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def duck_con():
    """Requires the pipeline to already be curated (docker up, scripts 01-03 run)."""
    from app.duck import connect
    return connect()
