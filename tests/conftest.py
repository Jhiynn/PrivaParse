from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from privaparse.app.config import Settings
from privaparse.database.repository import Database, VaultRepository
from privaparse.engine import PrivaParseEngine
from privaparse.parser.detector import CompositeDetector, Detector, RegexDetector
from privaparse.parser.types import SOURCE_GLINER, EntityType, Span

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture(autouse=True, scope="session")
def _ignore_local_env_file():
    """Keep the developer's `.env` out of the test run.

    `Settings` reads `.env` by design, which means without this the suite passes
    or fails depending on whose machine it runs on — a local
    `PRIVAPARSE_OFFLINE=true` is enough to break a test asserting the default.
    Tests must describe the code, not the checkout.
    """
    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    yield
    Settings.model_config["env_file"] = original


@pytest.fixture(autouse=True)
def _ignore_privaparse_env_vars(monkeypatch: pytest.MonkeyPatch):
    """Same reasoning for exported PRIVAPARSE_* variables."""
    for name in list(os.environ):
        if name.startswith("PRIVAPARSE_"):
            monkeypatch.delenv(name, raising=False)

#: Names the fake detector knows. Keeping this explicit means the pipeline tests
#: fail for pipeline reasons, never because a model changed its mind.
KNOWN_NAMES = (
    "Max Mustermann",
    "Erika Musterfrau",
    "Müller-Lüdenscheidt",
    "Anna Maria Schmidt",
)


class NameListDetector:
    """Stands in for GLiNER2 in tests: finds names from a fixed list.

    Deliberately behaves like the real thing — word-bounded, scored, tagged as
    coming from the model — so the merge and resolve steps are exercised for
    real without loading 800 MB of weights.
    """

    def __init__(self, names: tuple[str, ...] = KNOWN_NAMES, score: float = 0.95) -> None:
        # Case-insensitive, like a real NER model: it finds "MAX MUSTERMANN" in
        # a shouty heading just as readily as the title-cased form.
        self.patterns = [
            (name, re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE))
            for name in names
        ]
        self.score = score

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for _name, pattern in self.patterns:
            for match in pattern.finditer(text):
                spans.append(
                    Span(
                        start=match.start(),
                        end=match.end(),
                        text=match.group(0),
                        type=EntityType.PERSON,
                        score=self.score,
                        source=SOURCE_GLINER,
                    )
                )
        return spans


@pytest.fixture()
def database() -> Database:
    db = Database.in_memory()
    yield db
    db.dispose()


@pytest.fixture()
def repo(database: Database) -> VaultRepository:
    with database.session() as session:
        yield VaultRepository(session)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "vault.db", device="cpu", detector="regex")


@pytest.fixture()
def fake_detector() -> Detector:
    """A hybrid detector with the model half replaced by a known-name matcher."""
    return CompositeDetector([NameListDetector(), RegexDetector()])


@pytest.fixture()
def engine(settings: Settings, fake_detector: Detector) -> PrivaParseEngine:
    eng = PrivaParseEngine(settings, detector=fake_detector, configure_logs=False)
    yield eng
    eng.close()


@pytest.fixture()
def beispiel_md() -> str:
    return (DATA_DIR / "beispiel.md").read_text(encoding="utf-8")


@pytest.fixture()
def mit_code_md() -> str:
    return (DATA_DIR / "mit_code.md").read_text(encoding="utf-8")
