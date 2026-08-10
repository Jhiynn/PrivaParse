"""The long-lived object that owns everything expensive.

PrivaParse is meant to end up as a permanently running service, so the model
must be loaded once and reused — never per call. Everything expensive (weights,
DB engine, resolved device) lives on :class:`PrivaParseEngine`; the pipeline
functions are pure and take what they need as arguments.

A service constructs one engine at startup. Scripts and tests can use the
module-level :func:`detect` / :func:`pseudonymize` / :func:`reverse`, which
share a lazily created default engine.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from privaparse.app.config import Settings, load_settings
from privaparse.app.device import ResolvedDevice, resolve_device
from privaparse.app.logging import configure_logging, get_logger
from privaparse.database.cipher import IdentityCipher, ValueCipher
from privaparse.database.repository import Database, VaultRepository

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.parser.detector import Detector
    from privaparse.parser.pseudonymizer import PseudonymizationResult
    from privaparse.parser.reverse_mapper import ReverseResult
    from privaparse.parser.types import Span

log = get_logger("engine")

__all__ = [
    "PrivaParseEngine",
    "default_engine",
    "reset_default_engine",
    "detect",
    "pseudonymize",
    "reverse",
]


class PrivaParseEngine:
    """Holds configuration, the vault and the (lazily loaded) detector."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        detector: "Detector | None" = None,
        cipher: ValueCipher | None = None,
        database: Database | None = None,
        configure_logs: bool = True,
        progress: "Callable[[int, int], None] | None" = None,
    ) -> None:
        #: Optional (chunks done, chunks total) callback, for a CLI progress bar.
        self.progress = progress
        self.settings = settings if settings is not None else load_settings()
        if configure_logs:
            configure_logging(self.settings.log_level)

        # Before anything can import huggingface_hub, which reads these once.
        _point_hf_cache_at(self.settings.model_dir)
        if self.settings.offline:
            _go_offline()

        self.device: ResolvedDevice = resolve_device(self.settings)
        self.cipher: ValueCipher = cipher if cipher is not None else IdentityCipher()

        self.database = database if database is not None else self._open_database()

        self._detector = detector
        self._detector_lock = threading.Lock()

        log.info(
            "engine ready: %s model=%s vault=%s",
            self.device.describe(),
            self.settings.model_id,
            self.settings.db_path,
        )

    # --- resources ---------------------------------------------------------

    def _open_database(self) -> Database:
        db = Database.from_path(Path(self.settings.db_path))
        # First run bootstrap. Alembic owns schema *changes*; this just makes a
        # fresh install usable without a separate command.
        db.create_all()
        return db

    def repository(self, session: Any) -> VaultRepository:
        return VaultRepository(session, self.cipher)

    # --- pipeline ----------------------------------------------------------

    def detect(self, text: str) -> list["Span"]:
        """Detected entities, after masking, merging and the coreference sweep.

        Read-only: nothing is written to the vault.
        """
        from privaparse.parser.markdown import protect
        from privaparse.parser.merge import resolve_spans

        protected = protect(text, scan_code=self.settings.scan_code)
        raw = self.detector.detect(protected.view)
        return resolve_spans(
            protected,
            raw,
            threshold=self.settings.threshold,
            sweep=self.settings.coreference_sweep,
            catalogue=self.settings.catalogue,
        )

    def pseudonymize(
        self, text: str, *, source_name: str | None = None
    ) -> "PseudonymizationResult":
        """Replace entities with placeholders and record a reversible mapping."""
        from privaparse.parser.pseudonymizer import pseudonymize_text

        with self.database.session() as session:
            return pseudonymize_text(
                text,
                detector=self.detector,
                repo=self.repository(session),
                settings=self.settings,
                source_name=source_name,
            )

    def reverse(
        self, mapping_id: str | None, text: str, *, strict: bool = False
    ) -> "ReverseResult":
        """Restore the placeholders this mapping issued — and only those.

        ``mapping_id=None`` looks for the session that issued *every*
        placeholder in the text. That is convenience, not a shortcut around the
        session boundary: partial coverage matches nothing.
        """
        from privaparse.parser.reverse_mapper import find_mapping_for, reverse_text

        with self.database.session() as session:
            repo = self.repository(session)
            resolved = mapping_id or find_mapping_for(text, repo=repo)
            return reverse_text(resolved, text, repo=repo, strict=strict)

    def vault_stats(self):
        with self.database.session() as session:
            return self.repository(session).stats()

    def recent_mappings(self, limit: int = 20, match: str | None = None):
        """Recent sessions and their mapping ids. Reveals no stored values."""
        with self.database.session() as session:
            return self.repository(session).recent_mappings(limit=limit, match=match)

    @property
    def detector(self) -> "Detector":
        """The detector, loaded on first use and kept warm afterwards."""
        if self._detector is None:
            with self._detector_lock:
                if self._detector is None:
                    self._detector = self._build_detector()
        return self._detector

    def _build_detector(self) -> "Detector":
        from privaparse.parser.detector import build_default_detector

        return build_default_detector(self.settings, self.device, progress=self.progress)

    def close(self) -> None:
        self.database.dispose()

    def __enter__(self) -> "PrivaParseEngine":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _point_hf_cache_at(model_dir: Path) -> None:
    """Keep downloaded weights inside the project rather than the user's home.

    Without this, ``model_dir`` is a setting that reads nicely and does nothing,
    and Hugging Face quietly downloads another 1.2 GB into
    ``~/.cache/huggingface`` — which is a real cost on a slow connection and a
    surprise on a machine where the project directory was chosen deliberately.

    An explicit ``HF_HOME`` in the environment wins: someone who set it meant it.
    """
    if os.environ.get("HF_HOME"):
        return

    resolved = Path(model_dir).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(resolved)
    log.debug("model cache: %s", resolved)


def _go_offline() -> None:
    """Cut the Hugging Face Hub out of startup entirely.

    Without this, every run reaches out to the Hub to revalidate the cached
    model — which is a strange thing for a tool whose whole promise is that the
    document never leaves the machine. Nothing sensitive is transmitted, but the
    connection is real, and on an air-gapped or audited machine it is the
    difference between "local" and "local, except".
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    log.debug("offline mode: the Hugging Face Hub will not be contacted")


# --- default engine --------------------------------------------------------

_default: PrivaParseEngine | None = None
_default_lock = threading.Lock()


def default_engine() -> PrivaParseEngine:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = PrivaParseEngine()
    return _default


def reset_default_engine() -> None:
    """Drop the shared engine. Mainly for tests and config reloads."""
    global _default
    with _default_lock:
        if _default is not None:
            _default.close()
        _default = None


# --- convenience API -------------------------------------------------------


def detect(text: str) -> list["Span"]:
    return default_engine().detect(text)


def pseudonymize(text: str, *, source_name: str | None = None) -> "PseudonymizationResult":
    return default_engine().pseudonymize(text, source_name=source_name)


def reverse(mapping_id: str, text: str, *, strict: bool = False) -> "ReverseResult":
    return default_engine().reverse(mapping_id, text, strict=strict)
