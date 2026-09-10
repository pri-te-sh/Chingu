"""pixels.archived: removing a Pixel hides it and unpairs it, history is kept

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None



def _has(col):
    """0001 creates tables from the *current* models, so on a fresh database later columns already exist."""
    return col in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pixels")}

def upgrade():
    if _has("archived"): return
    op.add_column("pixels", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("pixels", "archived")
