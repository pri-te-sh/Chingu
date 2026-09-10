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



def _has(col):
    """0001 creates tables from the *current* models, so on a fresh database later columns already exist."""
    return col in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pixels")}

def upgrade():
    if _has("paired_at"): return
    op.alter_column("pixels", "household_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("pixels", sa.Column("paired_at", sa.DateTime(timezone=True)))
    op.create_index("ix_pixels_pairing_code", "pixels", ["pairing_code"])


def downgrade():
    op.drop_index("ix_pixels_pairing_code")
    op.drop_column("pixels", "paired_at")
    op.alter_column("pixels", "household_id", existing_type=sa.BigInteger(), nullable=False)
