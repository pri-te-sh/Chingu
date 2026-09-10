"""remember the previous household across unpair so ownership transfer can reset the persona

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision, down_revision = "0005", "0004"
branch_labels = depends_on = None


def upgrade():
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pixels")}
    if "prev_household_id" not in cols: op.add_column("pixels", sa.Column("prev_household_id", sa.BigInteger))


def downgrade():
    op.drop_column("pixels", "prev_household_id")
