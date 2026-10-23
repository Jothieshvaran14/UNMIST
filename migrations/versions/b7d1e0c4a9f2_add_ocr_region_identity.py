"""add ocr region identity for idempotent persistence

Adds ``ocr_regions.region_uid`` and a unique ``(page_id, region_uid)``
constraint. This is what makes OCR persistence idempotent: a redelivered or
retried ``document.ocr`` job recomputes the same deterministic uid per region
and updates the existing row in place rather than inserting a duplicate.

Additive and nullable: rows written before this migration keep ``NULL`` uid and
are excluded from the uniqueness rule by the usual SQL NULL semantics on SQLite
and PostgreSQL, so existing data is preserved unchanged.

Revision ID: b7d1e0c4a9f2
Revises: cfa0da8f8208
Create Date: 2026-10-19 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d1e0c4a9f2'
down_revision: Union[str, None] = 'cfa0da8f8208'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ocr_regions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('region_uid', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_region_page_uid', ['page_id', 'region_uid'])


def downgrade() -> None:
    with op.batch_alter_table('ocr_regions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_region_page_uid', type_='unique')
        batch_op.drop_column('region_uid')
