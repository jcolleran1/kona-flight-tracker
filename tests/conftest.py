"""Shared pytest setup. Tests never touch the network or a real display."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

FIXTURES = ROOT / "data" / "fixtures"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any accidental network call in a test is a bug: fail loudly."""
    import urllib.request

    def _blocked(*a, **k):
        raise AssertionError("test tried to open a network connection")
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


@pytest.fixture(scope="session")
def cfg():
    from config import load_config
    return load_config(local=False)   # the committed file, not this machine's overlay


@pytest.fixture(scope="session")
def nasty() -> dict[str, dict]:
    """The awkward-records fixture, keyed by hex."""
    with open(FIXTURES / "nasty_cases.json", encoding="utf-8") as f:
        return {r["hex"]: r for r in json.load(f)["ac"]}
