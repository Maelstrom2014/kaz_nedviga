import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _stop_scheduler_between_tests():
    """Stop any background scheduler thread before/after each test so the
    daemon can't escape into the next test (it would call real parsers
    after monkeypatch is undone) and can't keep running past the suite."""
    try:
        import scheduler
        scheduler.reset()
    except Exception:
        pass
    yield
    try:
        import scheduler
        scheduler.reset()
    except Exception:
        pass
