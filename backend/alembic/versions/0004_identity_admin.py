"""device identity keys + platform admins

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision, down_revision = "0004", "0003"
branch_labels = depends_on = None


def _cols(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "device_key_hash" not in _cols("pixels"): op.add_column("pixels", sa.Column("device_key_hash", sa.Text))
    if "is_admin" not in _cols("users"): op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("pixels", "device_key_hash"); op.drop_column("users", "is_admin")
