"""Data access for the vault.

Everything that touches a stored value goes through :class:`ValueCipher` here,
so no caller ever handles the on-disk representation directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from privaparse.app.logging import get_logger, register_secret
from privaparse.database.cipher import IdentityCipher, ValueCipher
from privaparse.database.models import Base, Entity, EntityValue, Mapping, MappingEntry
from privaparse.database.placeholder import build_placeholder

log = get_logger("database")

_MAX_SUFFIX_RETRIES = 5


@dataclass(frozen=True)
class MappingSummary:
    """One pseudonymisation session, described without revealing its contents."""

    id: str
    created_at: datetime
    source_name: str | None
    placeholders: int


@dataclass(frozen=True)
class VaultStats:
    entities: int
    surface_forms: int
    mappings: int
    by_type: dict[str, int]


class Database:
    """Owns the engine and session factory. One instance per process."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._engine: Engine = create_engine(url, echo=echo, future=True)
        _enable_sqlite_foreign_keys(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    @classmethod
    def from_path(cls, path: Path, *, echo: bool = False) -> "Database":
        path = Path(path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{path.resolve()}", echo=echo)

    @classmethod
    def in_memory(cls) -> "Database":
        db = cls("sqlite://")
        db.create_all()
        return db

    @property
    def engine(self) -> Engine:
        return self._engine

    def create_all(self) -> None:
        """Create tables directly. Alembic owns schema changes; this is for tests
        and first-run bootstrap."""
        Base.metadata.create_all(self._engine)

    def session(self) -> Session:
        return self._session_factory()

    def dispose(self) -> None:
        self._engine.dispose()


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class VaultRepository:
    """Reads and writes the vault. Not thread-safe; use one per session."""

    def __init__(self, session: Session, cipher: ValueCipher | None = None) -> None:
        self.session = session
        self.cipher = cipher if cipher is not None else IdentityCipher()

    # --- entities ----------------------------------------------------------

    def find_entity(self, entity_type: str, normalized_value: str) -> Entity | None:
        stored = self.cipher.encrypt_key(normalized_value)
        stmt = select(Entity).where(
            Entity.type == entity_type, Entity.normalized_value == stored
        )
        return self.session.scalars(stmt).one_or_none()

    def get_or_create_entity(self, entity_type: str, normalized_value: str) -> Entity:
        """Return the vault entry for this value, creating it on first sight.

        Placeholder allocation races are resolved by retrying: the UNIQUE
        constraint on ``suffix_index`` is what actually guarantees uniqueness,
        not the ``MAX() + 1`` read.
        """
        existing = self.find_entity(entity_type, normalized_value)
        if existing is not None:
            return existing

        stored = self.cipher.encrypt_key(normalized_value)
        for attempt in range(_MAX_SUFFIX_RETRIES):
            index = self._next_suffix_index()
            entity = Entity(
                type=entity_type,
                normalized_value=stored,
                placeholder=build_placeholder(entity_type, index),
                suffix_index=index,
            )
            try:
                # SAVEPOINT, not a plain flush: a bare rollback here would undo
                # every entity already created for this document, not just the
                # one that collided.
                with self.session.begin_nested():
                    self.session.add(entity)
                    self.session.flush()
            except IntegrityError:
                # Either we lost the suffix race, or another writer inserted the
                # same value first. Both are resolved by looking again.
                existing = self.find_entity(entity_type, normalized_value)
                if existing is not None:
                    return existing
                log.debug(
                    "suffix collision on index %d, retry %d/%d",
                    index,
                    attempt + 1,
                    _MAX_SUFFIX_RETRIES,
                )
                continue
            log.debug("vault: new entity %s (type=%s)", entity.placeholder, entity_type)
            return entity

        raise RuntimeError(
            f"could not allocate a placeholder after {_MAX_SUFFIX_RETRIES} attempts"
        )

    def _next_suffix_index(self) -> int:
        current = self.session.scalar(select(func.max(Entity.suffix_index)))
        return 0 if current is None else int(current) + 1

    def entity_by_placeholder(self, placeholder: str) -> Entity | None:
        stmt = select(Entity).where(Entity.placeholder == placeholder)
        return self.session.scalars(stmt).one_or_none()

    def normalized_value_of(self, entity: Entity) -> str:
        return self.cipher.decrypt_key(entity.normalized_value)

    # --- surface forms -----------------------------------------------------

    def record_surface_form(self, entity: Entity, original_value: str) -> EntityValue:
        stored = self.cipher.encrypt_value(original_value)
        stmt = select(EntityValue).where(
            EntityValue.entity_id == entity.id, EntityValue.original_value == stored
        )
        found = self.session.scalars(stmt).one_or_none()
        if found is not None:
            return found

        value = EntityValue(entity_id=entity.id, original_value=stored)
        try:
            with self.session.begin_nested():
                self.session.add(value)
                self.session.flush()
        except IntegrityError:
            found = self.session.scalars(stmt).one_or_none()
            if found is None:
                raise
            return found
        return value

    def surface_value_of(self, value: EntityValue) -> str:
        return self.cipher.decrypt_value(value.original_value)

    # --- mappings ----------------------------------------------------------

    def create_mapping(self, *, text_sha256: str, source_name: str | None = None) -> Mapping:
        mapping = Mapping(text_sha256=text_sha256, source_name=source_name)
        self.session.add(mapping)
        self.session.flush()
        return mapping

    def add_mapping_entry(
        self,
        mapping: Mapping,
        entity: Entity,
        restore_value: EntityValue,
        occurrences: int = 1,
    ) -> MappingEntry:
        entry = MappingEntry(
            mapping_id=mapping.id,
            entity_id=entity.id,
            restore_value_id=restore_value.id,
            occurrences=occurrences,
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def get_mapping(self, mapping_id: str) -> Mapping | None:
        return self.session.get(Mapping, mapping_id)

    def restore_table(self, mapping_id: str) -> dict[str, str]:
        """``{placeholder: original spelling}`` for exactly this session.

        Placeholders outside this mapping are deliberately absent — see the
        module docstring of :mod:`privaparse.database.models`.
        """
        stmt = (
            select(Entity.placeholder, EntityValue.original_value)
            .join(MappingEntry, MappingEntry.entity_id == Entity.id)
            .join(EntityValue, EntityValue.id == MappingEntry.restore_value_id)
            .where(MappingEntry.mapping_id == mapping_id)
        )
        table: dict[str, str] = {}
        for placeholder, stored in self.session.execute(stmt):
            value = self.cipher.decrypt_value(stored)
            register_secret(value)
            table[placeholder] = value
        return table

    def find_covering_mappings(
        self, placeholders: set[str], limit: int = 50
    ) -> list[MappingSummary]:
        """Sessions that issued **every** placeholder in ``placeholders``.

        Requiring full coverage is what keeps this from weakening the session
        boundary. A document carrying one placeholder from somewhere else — an
        injected one, or a hallucinated one — is covered by no session at all,
        so the search fails and the caller has to name a mapping explicitly.
        At that point the foreign placeholder is refused by the normal rule.
        """
        if not placeholders:
            return []

        stmt = select(Mapping).order_by(Mapping.created_at.desc()).limit(limit)
        covering: list[MappingSummary] = []

        for mapping in self.session.scalars(stmt):
            issued = {entry.entity.placeholder for entry in mapping.entries}
            if placeholders <= issued:
                covering.append(
                    MappingSummary(
                        id=mapping.id,
                        created_at=mapping.created_at,
                        source_name=mapping.source_name,
                        placeholders=len(mapping.entries),
                    )
                )
        return covering

    def placeholder_is_known(self, placeholder: str) -> bool:
        """True if the vault has ever issued this placeholder, to any document."""
        stmt = select(func.count()).select_from(Entity).where(Entity.placeholder == placeholder)
        return bool(self.session.scalar(stmt))

    # --- diagnostics -------------------------------------------------------

    def stats(self) -> VaultStats:
        by_type_rows = self.session.execute(
            select(Entity.type, func.count()).group_by(Entity.type)
        ).all()
        return VaultStats(
            entities=int(self.session.scalar(select(func.count()).select_from(Entity)) or 0),
            surface_forms=int(
                self.session.scalar(select(func.count()).select_from(EntityValue)) or 0
            ),
            mappings=int(self.session.scalar(select(func.count()).select_from(Mapping)) or 0),
            by_type={str(t): int(c) for t, c in by_type_rows},
        )

    def iter_mappings(self, limit: int = 20) -> Iterator[Mapping]:
        stmt = select(Mapping).order_by(Mapping.created_at.desc()).limit(limit)
        yield from self.session.scalars(stmt)

    def recent_mappings(
        self, limit: int = 20, match: str | None = None
    ) -> list[MappingSummary]:
        """Recent sessions, newest first. Returns no stored values."""
        stmt = select(Mapping).order_by(Mapping.created_at.desc())
        if match:
            stmt = stmt.where(Mapping.source_name.ilike(f"%{match}%"))
        stmt = stmt.limit(limit)

        return [
            MappingSummary(
                id=mapping.id,
                created_at=mapping.created_at,
                source_name=mapping.source_name,
                placeholders=len(mapping.entries),
            )
            for mapping in self.session.scalars(stmt)
        ]
