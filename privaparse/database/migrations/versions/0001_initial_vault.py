"""Initial vault schema: entities, entity_values, mappings, mapping_entries.

Revision ID: 0001
Revises:
Create Date: 2026-08-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("placeholder", sa.String(length=64), nullable=False),
        sa.Column("suffix_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "normalized_value", name="uq_entities_type_value"),
        sa.UniqueConstraint("placeholder", name="uq_entities_placeholder"),
        sa.UniqueConstraint("suffix_index", name="uq_entities_suffix_index"),
    )
    op.create_index("ix_entities_lookup", "entities", ["type", "normalized_value"])

    op.create_table(
        "entity_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("original_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "original_value", name="uq_entity_values_surface"),
    )

    op.create_table(
        "mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_name", sa.String(length=512), nullable=True),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "mapping_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mapping_id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("restore_value_id", sa.Integer(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["mapping_id"], ["mappings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["restore_value_id"], ["entity_values.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mapping_id", "entity_id", name="uq_mapping_entries_pair"),
    )
    op.create_index("ix_mapping_entries_mapping", "mapping_entries", ["mapping_id"])


def downgrade() -> None:
    op.drop_index("ix_mapping_entries_mapping", table_name="mapping_entries")
    op.drop_table("mapping_entries")
    op.drop_table("mappings")
    op.drop_table("entity_values")
    op.drop_index("ix_entities_lookup", table_name="entities")
    op.drop_table("entities")
