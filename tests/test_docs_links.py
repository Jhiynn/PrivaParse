"""Every documentation reference points at a file that exists.

Two kinds of reference rot are possible here and both have bitten this
repository's shape already: a Markdown link between docs, and a bare
``docs/...md`` path quoted in a source comment. The second kind is the
dangerous one -- nothing renders it, so nothing reveals it is dead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Local scratch (`.superpowers`, `.claude`) is git-ignored and disposable: the
# notes inside quote documentation paths as examples, so a file move would
# fail this test on throwaway scaffolding rather than on anything shipped.
# `docs/superpowers` is untracked for a different reason -- it holds internal
# plans and specs, kept on disk but not published -- but the same mismatch
# applies: `rglob` still walks it, so it needs the same exclusion or this
# test would keep checking documents that no longer ship.
SKIP_DIRS = {
    ".venv", ".git", ".idea", ".claude", ".superpowers",
    "node_modules", ".pytest_cache", ".ruff_cache", "build", "dist",
    "superpowers",
}

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
# A path, not a regex. Prose in these documents quotes patterns like
# `[\d{1,2}](\d{4})`, which is link syntax to a naive reader and nothing of
# the sort to a human, so anything carrying regex punctuation is not a link.
PATH_LIKE = re.compile(r"^[A-Za-z0-9_.\-/]+$")
# No `...` -- this file's own docstring says `docs/...md` while describing the
# rule, and a checker that fails on its own explanation is a checker nobody
# keeps.
DOC_PATH = re.compile(r"docs/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.md")


def _walk(suffixes: tuple[str, ...]) -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if path.suffix not in suffixes or not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return sorted(found)


def _identifier(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("markdown", _walk((".md",)), ids=_identifier)
def test_markdown_links_resolve(markdown: Path) -> None:
    missing = []
    for match in MARKDOWN_LINK.finditer(markdown.read_text(encoding="utf-8")):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if not target or not PATH_LIKE.match(target):
            continue
        if not (markdown.parent / target).resolve().exists():
            missing.append(target)
    assert not missing, f"{_identifier(markdown)} links to missing: {sorted(set(missing))}"


@pytest.mark.parametrize("source", _walk((".py", ".yaml", ".yml", ".toml", ".sh")), ids=_identifier)
def test_doc_paths_quoted_in_source_resolve(source: Path) -> None:
    missing = [
        quoted
        for quoted in DOC_PATH.findall(source.read_text(encoding="utf-8"))
        if not (ROOT / quoted).exists()
    ]
    assert not missing, f"{_identifier(source)} cites missing: {sorted(set(missing))}"
