"""Persist operator handoff requests.

Revision ID: 20260920_0004
Revises: 20260920_0003
"""

from alembic import op
import sqlalchemy as sa

revision = "20260920_0004"
down_revision = "20260920_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "support_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), server_default="new", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_support_requests"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_support_requests_user_id_users"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name="fk_support_requests_conversation_id_conversations"),
        sa.CheckConstraint("status IN ('new', 'in_progress', 'resolved', 'cancelled')", name="ck_support_requests_status"),
    )
    for column in ("user_id", "conversation_id", "status"):
        op.create_index(f"ix_support_requests_{column}", "support_requests", [column])
    op.create_index(
        "uq_support_requests_active_conversation", "support_requests", ["conversation_id"], unique=True,
        postgresql_where=sa.text("status IN ('new', 'in_progress')"),
        sqlite_where=sa.text("status IN ('new', 'in_progress')"),
    )


def downgrade():
    op.drop_index("uq_support_requests_active_conversation", table_name="support_requests")
    for column in ("status", "conversation_id", "user_id"):
        op.drop_index(f"ix_support_requests_{column}", table_name="support_requests")
    op.drop_table("support_requests")
