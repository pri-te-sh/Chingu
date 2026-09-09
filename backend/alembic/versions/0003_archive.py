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


def upgrade():
    op.add_column("pixels", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("pixels", "archived")
