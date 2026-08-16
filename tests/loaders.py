from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Load an HTML fixture file by name (without .html extension)."""
    path = FIXTURES_DIR / f"{name}.html"
    return path.read_text(encoding="utf-8")


def load_json_fixture(name: str):
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))
