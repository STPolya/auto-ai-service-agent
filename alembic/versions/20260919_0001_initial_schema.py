"""Create users, vehicles, services, and appointments.

Revision ID: 20260919_0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "20260919_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("brand", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("license_plate", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_vehicles_user_id_users"),
    )
    op.create_index("ix_vehicles_user_id", "vehicles", ["user_id"])
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_from", sa.Numeric(12, 2), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_services_name"),
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("appointment_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), server_default="scheduled", nullable=False),
        sa.Column("problem_description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_appointments_user_id_users"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], name="fk_appointments_vehicle_id_vehicles"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], name="fk_appointments_service_id_services"),
    )
    for column in ("user_id", "vehicle_id", "service_id", "appointment_at"):
        op.create_index(f"ix_appointments_{column}", "appointments", [column])


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("services")
    op.drop_table("vehicles")
    op.drop_table("users")
