"""initial schema

Phase 0 contract: this migration must stay 1:1 with app/models.py.

Revision ID: 0001
Revises: 
Create Date: 2026-08-26 12:01:52.519812
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('value', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('llm_connections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('base_url', sa.String(length=512), nullable=True),
    sa.Column('encrypted_api_key', sa.Text(), nullable=True),
    sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_index('ix_llm_connections_is_default', 'llm_connections', ['is_default'], unique=False)

    op.create_table('loki_connections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('source_type', sa.String(length=32), server_default='loki', nullable=False),
    sa.Column('base_url', sa.String(length=512), nullable=False),
    sa.Column('auth_type', sa.String(length=32), server_default='none', nullable=False),
    sa.Column('encrypted_secret', sa.Text(), nullable=True),
    sa.Column('label_mapping', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default='{}', nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('analysis_policies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('loki_connection_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('logql', sa.Text(), nullable=False),
    sa.Column('default_range_minutes', sa.Integer(), server_default='60', nullable=False),
    sa.Column('max_lines', sa.Integer(), server_default='1000', nullable=False),
    sa.Column('exclusions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default='[]', nullable=False),
    sa.Column('max_samples_per_group', sa.Integer(), server_default='3', nullable=False),
    sa.Column('allow_ai_analysis', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('daily_analysis_limit', sa.Integer(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.CheckConstraint('max_lines > 0', name='ck_analysis_policies_max_lines'),
    sa.CheckConstraint('max_samples_per_group > 0', name='ck_analysis_policies_max_samples_per_group'),
    sa.ForeignKeyConstraint(['loki_connection_id'], ['loki_connections.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_analysis_policies_name')
    )
    op.create_table('query_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('policy_id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('range_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('range_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='pending', nullable=False),
    sa.Column('fetched_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('dropped_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('warnings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default='[]', nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['policy_id'], ['analysis_policies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_query_runs_policy_started', 'query_runs', ['policy_id', 'started_at'], unique=False)

    op.create_table('error_groups',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('query_run_id', sa.Integer(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('service', sa.String(length=128), nullable=True),
    sa.Column('environment', sa.String(length=64), nullable=True),
    sa.Column('error_type', sa.String(length=255), nullable=True),
    sa.Column('normalized_message', sa.Text(), nullable=False),
    sa.Column('count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('labels', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default='{}', nullable=False),
    sa.Column('top_stack_frame', sa.Text(), nullable=True),
    sa.Column('normalization_rule_version', sa.String(length=32), server_default='v1', nullable=False),
    sa.ForeignKeyConstraint(['query_run_id'], ['query_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('query_run_id', 'fingerprint', name='uq_error_groups_run_fingerprint')
    )
    op.create_index('ix_error_groups_fingerprint', 'error_groups', ['fingerprint'], unique=False)
    op.create_index('ix_error_groups_service_last_seen', 'error_groups', ['service', 'last_seen'], unique=False)

    op.create_table('analysis_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('error_group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('llm_connection_id', sa.Integer(), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='pending', nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), server_default='v1', nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['error_group_id'], ['error_groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['llm_connection_id'], ['llm_connections.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_analysis_jobs_fingerprint', 'analysis_jobs', ['fingerprint'], unique=False)
    op.create_index('ix_analysis_jobs_group_status', 'analysis_jobs', ['error_group_id', 'status'], unique=False)
    op.create_index('ix_analysis_jobs_requested_at', 'analysis_jobs', ['requested_at'], unique=False)

    op.create_table('error_samples',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('error_group_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('masked_log', sa.Text(), nullable=False),
    sa.Column('labels', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), server_default='{}', nullable=False),
    sa.Column('stacktrace', sa.Text(), nullable=True),
    sa.Column('masking_rule_version', sa.String(length=32), server_default='v1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['error_group_id'], ['error_groups.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_error_samples_created_at', 'error_samples', ['created_at'], unique=False)
    op.create_index('ix_error_samples_group_occurred', 'error_samples', ['error_group_id', 'occurred_at'], unique=False)

    op.create_table('analysis_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('analysis_job_id', sa.Integer(), nullable=False),
    sa.Column('result_json', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('severity', sa.String(length=16), server_default='medium', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['analysis_job_id'], ['analysis_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('analysis_job_id')
    )
    op.create_table('llm_usage_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('analysis_job_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('estimated_cost', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.Column('pricing_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=32), server_default='succeeded', nullable=False),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['analysis_job_id'], ['analysis_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('analysis_job_id')
    )
    op.create_index('ix_llm_usage_records_model_created', 'llm_usage_records', ['model', 'created_at'], unique=False)

def downgrade() -> None:
    op.drop_index('ix_llm_usage_records_model_created', table_name='llm_usage_records')

    op.drop_table('llm_usage_records')
    op.drop_table('analysis_results')
    op.drop_index('ix_error_samples_group_occurred', table_name='error_samples')
    op.drop_index('ix_error_samples_created_at', table_name='error_samples')

    op.drop_table('error_samples')
    op.drop_index('ix_analysis_jobs_requested_at', table_name='analysis_jobs')
    op.drop_index('ix_analysis_jobs_group_status', table_name='analysis_jobs')
    op.drop_index('ix_analysis_jobs_fingerprint', table_name='analysis_jobs')

    op.drop_table('analysis_jobs')
    op.drop_index('ix_error_groups_service_last_seen', table_name='error_groups')
    op.drop_index('ix_error_groups_fingerprint', table_name='error_groups')

    op.drop_table('error_groups')
    op.drop_index('ix_query_runs_policy_started', table_name='query_runs')

    op.drop_table('query_runs')
    op.drop_table('analysis_policies')
    op.drop_table('loki_connections')
    op.drop_index('ix_llm_connections_is_default', table_name='llm_connections')

    op.drop_table('llm_connections')
    op.drop_table('app_settings')
