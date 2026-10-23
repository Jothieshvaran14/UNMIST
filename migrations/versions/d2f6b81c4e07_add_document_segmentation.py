"""add document segmentation tables and lifecycle columns

Checkpoint E turns persisted OCR regions into reviewable question/answer
structure. Adds three tables and a small set of ``source_documents`` columns.

New tables
----------
* ``document_segments`` — one structural candidate (question, subpart, answer,
  page furniture, diagram, equation, table, unclassified) with a deterministic
  ``segment_uid``, optional parent, page span, reading order, an anchor box on
  ``start_page`` only, assembled text, categorical ``uncertainty``, algorithm
  provenance, and review state. Unique ``(document_id, segment_uid)`` makes
  retries idempotent.
* ``segment_regions`` — the evidence link: which OCR regions constitute a
  segment, in order. ``UNIQUE(region_id)`` enforces the rule that one OCR
  region belongs to at most one segment, so two structures can never claim the
  same line.
* ``segment_reviews`` — append-only human confirmation/correction history,
  mirroring ``ocr_reviews``.

New ``source_documents`` columns
--------------------------------
``segmentation_status`` (default ``not_run``), ``segmentation_version``,
``segmentation_error_category``, ``segmentation_error_detail``,
``last_segment_job_id``, ``segmentation_run``. Segmentation deliberately has
its own status axis: ``processing_state`` still describes preprocessing/OCR and
``completed`` is terminal there, so segmentation must never mutate it.

Additive and backward compatible: new columns are defaulted or nullable, the
new tables only reference existing ones, and no existing row or constraint
changes.

Revision ID: d2f6b81c4e07
Revises: b7d1e0c4a9f2
Create Date: 2026-10-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2f6b81c4e07'
down_revision: Union[str, None] = 'b7d1e0c4a9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- source_documents: independent segmentation status axis -----------
    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('segmentation_status', sa.String(length=30), nullable=False, server_default=sa.text("'not_run'")))
        batch_op.add_column(sa.Column('segmentation_version', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('segmentation_error_category', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('segmentation_error_detail', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('last_segment_job_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('segmentation_run', sa.Integer(), nullable=False, server_default=sa.text('0')))
        batch_op.create_index(batch_op.f('ix_source_documents_segmentation_status'), ['segmentation_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_source_documents_last_segment_job_id'), ['last_segment_job_id'], unique=False)
        batch_op.create_foreign_key('fk_source_documents_last_segment_job_id', 'jobs', ['last_segment_job_id'], ['id'], ondelete='SET NULL')

    # --- document_segments ------------------------------------------------
    op.create_table(
        'document_segments',
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('segment_uid', sa.String(length=64), nullable=False),
        sa.Column('parent_id', sa.String(length=36), nullable=True),
        sa.Column('segment_type', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(length=60), nullable=True),
        sa.Column('start_page', sa.Integer(), nullable=False),
        sa.Column('end_page', sa.Integer(), nullable=False),
        sa.Column('reading_order', sa.Integer(), nullable=False),
        sa.Column('bbox_left', sa.Integer(), nullable=False),
        sa.Column('bbox_top', sa.Integer(), nullable=False),
        sa.Column('bbox_width', sa.Integer(), nullable=False),
        sa.Column('bbox_height', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('corrected_text', sa.Text(), nullable=True),
        sa.Column('uncertainty', sa.String(length=40), nullable=False),
        sa.Column('algorithm', sa.String(length=60), nullable=False),
        sa.Column('algorithm_version', sa.String(length=40), nullable=False),
        sa.Column('review_status', sa.String(length=30), nullable=False),
        sa.Column('reviewed_by', sa.String(length=36), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provenance', sa.JSON(), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint('bbox_width >= 0 AND bbox_height >= 0', name='ck_segment_bbox_nonneg'),
        sa.CheckConstraint('start_page >= 1 AND end_page >= start_page', name='ck_segment_page_span'),
        sa.ForeignKeyConstraint(['document_id'], ['source_documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['document_segments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'segment_uid', name='uq_segment_doc_uid'),
    )
    with op.batch_alter_table('document_segments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_segments_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_segments_parent_id'), ['parent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_segments_segment_type'), ['segment_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_segments_uncertainty'), ['uncertainty'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_segments_review_status'), ['review_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_segments_reviewed_by'), ['reviewed_by'], unique=False)
        batch_op.create_index('ix_segment_doc_order', ['document_id', 'reading_order'], unique=False)
        batch_op.create_index('ix_segment_doc_page', ['document_id', 'start_page'], unique=False)

    # --- segment_regions --------------------------------------------------
    op.create_table(
        'segment_regions',
        sa.Column('segment_id', sa.String(length=36), nullable=False),
        sa.Column('region_id', sa.String(length=36), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.CheckConstraint('position >= 0', name='ck_segment_region_position_nonneg'),
        sa.ForeignKeyConstraint(['region_id'], ['ocr_regions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['segment_id'], ['document_segments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('region_id', name='uq_segment_region_single_owner'),
        sa.UniqueConstraint('segment_id', 'position', name='uq_segment_region_position'),
    )
    with op.batch_alter_table('segment_regions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_segment_regions_segment_id'), ['segment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_segment_regions_region_id'), ['region_id'], unique=False)

    # --- segment_reviews --------------------------------------------------
    op.create_table(
        'segment_reviews',
        sa.Column('segment_id', sa.String(length=36), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('previous_text', sa.Text(), nullable=True),
        sa.Column('corrected_text', sa.Text(), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reviewer_id', sa.String(length=36), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.CheckConstraint('revision >= 1', name='ck_segment_review_revision_positive'),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['segment_id'], ['document_segments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('segment_id', 'revision', name='uq_segment_revision'),
    )
    with op.batch_alter_table('segment_reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_segment_reviews_segment_id'), ['segment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_segment_reviews_reviewer_id'), ['reviewer_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('segment_reviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_segment_reviews_reviewer_id'))
        batch_op.drop_index(batch_op.f('ix_segment_reviews_segment_id'))
    op.drop_table('segment_reviews')

    with op.batch_alter_table('segment_regions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_segment_regions_region_id'))
        batch_op.drop_index(batch_op.f('ix_segment_regions_segment_id'))
    op.drop_table('segment_regions')

    with op.batch_alter_table('document_segments', schema=None) as batch_op:
        batch_op.drop_index('ix_segment_doc_page')
        batch_op.drop_index('ix_segment_doc_order')
        batch_op.drop_index(batch_op.f('ix_document_segments_reviewed_by'))
        batch_op.drop_index(batch_op.f('ix_document_segments_review_status'))
        batch_op.drop_index(batch_op.f('ix_document_segments_uncertainty'))
        batch_op.drop_index(batch_op.f('ix_document_segments_segment_type'))
        batch_op.drop_index(batch_op.f('ix_document_segments_parent_id'))
        batch_op.drop_index(batch_op.f('ix_document_segments_document_id'))
    op.drop_table('document_segments')

    with op.batch_alter_table('source_documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_source_documents_last_segment_job_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_source_documents_last_segment_job_id'))
        batch_op.drop_index(batch_op.f('ix_source_documents_segmentation_status'))
        batch_op.drop_column('segmentation_run')
        batch_op.drop_column('last_segment_job_id')
        batch_op.drop_column('segmentation_error_detail')
        batch_op.drop_column('segmentation_error_category')
        batch_op.drop_column('segmentation_version')
        batch_op.drop_column('segmentation_status')
