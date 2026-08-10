"""SQLAlchemy models for the local vault.

Four tables, and the reason for each:

``entities``
    The vault itself. One row per distinct PII value, keyed by
    ``(type, normalized_value)``. This is what makes a placeholder stable across
    documents — the same person gets the same placeholder forever.

``entity_values``
    The surface forms actually observed for an entity (``Max Mustermann``,
    ``MAX MUSTERMANN``, ``Dr. Max Mustermann``). Needed because restoring has to
    put back a specific spelling, not a normalized one.

``mappings``
    One row per ``pseudonymize()`` call.

``mapping_entries``
    Which placeholders a given document was actually issued, and which surface
    form it gets back. This is the security boundary for ``reverse()``: without
    it, anyone could write ``[[PERSON_A47]]`` into a document and read a
    stranger's name straight out of the global vault.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Entity(Base):
    """A distinct PII value in the global vault."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stored via ValueCipher.encrypt_key — must stay deterministic, it is a key.
    normalized_value: Mapped[str] = mapped_column(Text, nullable=False)
    placeholder: Mapped[str] = mapped_column(String(64), nullable=False)
    suffix_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    values: Mapped[list["EntityValue"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("type", "normalized_value", name="uq_entities_type_value"),
        UniqueConstraint("placeholder", name="uq_entities_placeholder"),
        UniqueConstraint("suffix_index", name="uq_entities_suffix_index"),
        Index("ix_entities_lookup", "type", "normalized_value"),
    )

    def __repr__(self) -> str:  # pragma: no cover - never render the value
        return f"<Entity {self.placeholder} type={self.type}>"


class EntityValue(Base):
    """One observed spelling of an entity."""

    __tablename__ = "entity_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    # Stored via ValueCipher.encrypt_value — never queried by content.
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    entity: Mapped[Entity] = relationship(back_populates="values")

    __table_args__ = (
        UniqueConstraint("entity_id", "original_value", name="uq_entity_values_surface"),
    )

    def __repr__(self) -> str:  # pragma: no cover - never render the value
        return f"<EntityValue id={self.id} entity={self.entity_id}>"


class Mapping(Base):
    """One pseudonymisation session."""

    __tablename__ = "mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    entries: Mapped[list["MappingEntry"]] = relationship(
        back_populates="mapping", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Mapping {self.id} entries={len(self.entries)}>"


class MappingEntry(Base):
    """A placeholder this document was issued, plus the spelling it restores to."""

    __tablename__ = "mapping_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mapping_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mappings.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    restore_value_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entity_values.id", ondelete="RESTRICT"), nullable=False
    )
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    mapping: Mapped[Mapping] = relationship(back_populates="entries")
    entity: Mapped[Entity] = relationship(lazy="selectin")
    restore_value: Mapped[EntityValue] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("mapping_id", "entity_id", name="uq_mapping_entries_pair"),
        Index("ix_mapping_entries_mapping", "mapping_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MappingEntry mapping={self.mapping_id} entity={self.entity_id}>"
