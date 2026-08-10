"""Evaluation and benchmarking.

Code lives in the package so ``privaparse eval`` works from any directory after
an install. The gold data stays outside it, under ``eval/gold/`` — it is
development material, not something to ship in a wheel.
"""

from __future__ import annotations

from pathlib import Path

#: Repository root, when running from a source checkout.
_REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_GOLD_DIR = _REPO_ROOT / "eval" / "gold"
DEFAULT_GOLD_PATH = DEFAULT_GOLD_DIR / "de_gold.jsonl"
DEFAULT_GOLD_SOURCE = DEFAULT_GOLD_DIR / "de_gold_source.md"
DEFAULT_REPORT_DIR = _REPO_ROOT / "docs"
