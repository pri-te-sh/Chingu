"""pairing: unpaired devices have no household

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("pixels", "household_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("pixels", sa.Column("paired_at", sa.DateTime(timezone=True)))
    op.create_index("ix_pixels_pairing_code", "pixels", ["pairing_code"])


def downgrade():
    op.drop_index("ix_pixels_pairing_code")
    op.drop_column("pixels", "paired_at")
    op.alter_column("pixels", "household_id", existing_type=sa.BigInteger(), nullable=False)
