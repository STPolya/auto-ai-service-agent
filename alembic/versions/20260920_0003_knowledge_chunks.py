"""AutoCare lexical knowledge base.

Revision ID: 20260920_0003
Revises: 20260920_0002
"""

from alembic import op
import sqlalchemy as sa

revision = "20260920_0003"
down_revision = "20260920_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
    )
    op.create_index("ix_knowledge_chunks_source", "knowledge_chunks", ["source"])
    # SQLite is used only for isolated structural tests; FTS is PostgreSQL-native.
    if op.get_context().dialect.name == "postgresql":
        op.create_index(
            "ix_knowledge_chunks_search", "knowledge_chunks",
            [sa.text("to_tsvector('russian'::regconfig, title || ' ' || content)")],
            postgresql_using="gin", postgresql_where=sa.text("is_active IS true"),
        )


def downgrade():
    if op.get_context().dialect.name == "postgresql":
        op.drop_index("ix_knowledge_chunks_search", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_source", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
