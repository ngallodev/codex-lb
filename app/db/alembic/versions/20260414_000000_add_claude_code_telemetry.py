"""add Claude Code telemetry tables and dashboard setting

Revision ID: 20260414_000000_add_claude_code_telemetry
Revises: 20260413_000000_add_accounts_blocked_at
Create Date: 2026-04-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260414_000000_add_claude_code_telemetry"
down_revision = "20260413_000000_add_accounts_blocked_at"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()

    settings_columns = _columns(bind, "dashboard_settings")
    if settings_columns and "show_claude_code_dashboard" not in settings_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "show_claude_code_dashboard",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    if not _has_table(bind, "claude_code_telemetry_state"):
        op.create_table(
            "claude_code_telemetry_state",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sessions_count", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cost_usage_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_input", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_output", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_cache_read", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_cache_creation", sa.Float(), nullable=False, server_default="0"),
            sa.Column("active_time_user_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("active_time_cli_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("lines_added", sa.Float(), nullable=False, server_default="0"),
            sa.Column("lines_removed", sa.Float(), nullable=False, server_default="0"),
            sa.Column("commits_count", sa.Float(), nullable=False, server_default="0"),
            sa.Column("pull_requests_count", sa.Float(), nullable=False, server_default="0"),
        )

    if not _has_table(bind, "claude_code_telemetry_buckets"):
        op.create_table(
            "claude_code_telemetry_buckets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("sessions_count", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cost_usage_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_input", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_output", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_cache_read", sa.Float(), nullable=False, server_default="0"),
            sa.Column("token_cache_creation", sa.Float(), nullable=False, server_default="0"),
            sa.Column("active_time_user_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("active_time_cli_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("lines_added", sa.Float(), nullable=False, server_default="0"),
            sa.Column("lines_removed", sa.Float(), nullable=False, server_default="0"),
            sa.Column("commits_count", sa.Float(), nullable=False, server_default="0"),
            sa.Column("pull_requests_count", sa.Float(), nullable=False, server_default="0"),
            sa.UniqueConstraint("bucket_start", name="uq_claude_code_telemetry_bucket_start"),
        )
        op.create_index(
            "ix_claude_code_telemetry_bucket_start",
            "claude_code_telemetry_buckets",
            ["bucket_start"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "claude_code_telemetry_buckets"):
        op.drop_index("ix_claude_code_telemetry_bucket_start", table_name="claude_code_telemetry_buckets")
        op.drop_table("claude_code_telemetry_buckets")

    if _has_table(bind, "claude_code_telemetry_state"):
        op.drop_table("claude_code_telemetry_state")

    settings_columns = _columns(bind, "dashboard_settings")
    if "show_claude_code_dashboard" in settings_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("show_claude_code_dashboard")
