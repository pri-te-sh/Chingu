"""Pixel platform schema (v1). SQLAlchemy Core tables - used by Alembic for migrations and by repo.py for queries."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

def _ts(name="ts", **kw):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, **kw)

users = sa.Table("users", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("provider", sa.Text, nullable=False),            # dev | google
    sa.Column("provider_id", sa.Text, nullable=False),
    sa.Column("email", sa.Text), sa.Column("name", sa.Text), sa.Column("avatar", sa.Text),
    _ts("created_at"),
    sa.UniqueConstraint("provider", "provider_id"))

households = sa.Table("households", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("owner_user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
    sa.Column("timezone", sa.Text, nullable=False, server_default="America/New_York"),
    sa.Column("location", sa.Text, server_default="Atlanta"),
    sa.Column("interests", sa.Text, server_default="tech, Formula 1, Toronto Raptors"),
    sa.Column("settings", JSONB, nullable=False, server_default="{}"),   # brief_enabled, brief_refresh_min, session_gap_min, memory_model, ...
    _ts("created_at"))

household_members = sa.Table("household_members", metadata,
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("role", sa.Text, nullable=False, server_default="member"))

pixels = sa.Table("pixels", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=True),   # NULL = unpaired
    sa.Column("device_id", sa.Text, nullable=False, unique=True),      # stable id from the device (MAC-derived) or "sim-..."
    sa.Column("device_type", sa.Text, nullable=False, server_default="lite"),  # lite | 3s | sim
    sa.Column("name", sa.Text, nullable=False, server_default="Pixel"),
    sa.Column("pairing_code", sa.Text), sa.Column("token_hash", sa.Text),
    sa.Column("capabilities", JSONB, nullable=False, server_default="{}"),
    sa.Column("fw_version", sa.Text), sa.Column("fw_channel", sa.Text, server_default="stable"), sa.Column("reset_reason", sa.Text),
    _ts("created_at"), sa.Column("last_seen_at", sa.DateTime(timezone=True)), sa.Column("paired_at", sa.DateTime(timezone=True)),
    sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()))

personas = sa.Table("personas", metadata,
    sa.Column("pixel_id", sa.BigInteger, sa.ForeignKey("pixels.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("config", JSONB, nullable=False, server_default="{}"))   # persona text, tone, eye_color, mood_colors, models, tools, barge-in ...

facts = sa.Table("facts", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("type", sa.Text, nullable=False, server_default="fact"),
    sa.Column("text", sa.Text, nullable=False),
    _ts("first_seen"), _ts("last_confirmed"),
    sa.Column("source_turn_id", sa.BigInteger),
    sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()))

followups = sa.Table("followups", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("text", sa.Text, nullable=False), sa.Column("due", sa.Text),
    _ts("created_at"), sa.Column("done", sa.Boolean, nullable=False, server_default=sa.false()))

summaries = sa.Table("summaries", metadata,
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("day", sa.Text, primary_key=True),
    sa.Column("text", sa.Text, nullable=False))

turns = sa.Table("turns", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("pixel_id", sa.BigInteger, sa.ForeignKey("pixels.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True),
    _ts(), sa.Column("user_text", sa.Text), sa.Column("reply", sa.Text),
    sa.Column("expr", sa.Text), sa.Column("intensity", sa.Float),
    sa.Column("t_expr", sa.Integer), sa.Column("t_audio", sa.Integer), sa.Column("t_done", sa.Integer),
    sa.Column("t_stt", sa.Integer), sa.Column("audio_s", sa.Float), sa.Column("model", sa.Text),
    sa.Column("tools", JSONB, nullable=False, server_default="[]"), sa.Column("steps", JSONB, nullable=False, server_default="[]"),
    sa.Column("error", sa.Text))

device_events = sa.Table("device_events", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("pixel_id", sa.BigInteger, sa.ForeignKey("pixels.id", ondelete="CASCADE"), nullable=False, index=True),
    _ts(), sa.Column("event", sa.Text, nullable=False), sa.Column("meta", JSONB, nullable=False, server_default="{}"))

device_logs = sa.Table("device_logs", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("pixel_id", sa.BigInteger, sa.ForeignKey("pixels.id", ondelete="CASCADE"), nullable=False, index=True),
    _ts(), sa.Column("level", sa.Text), sa.Column("line", sa.Text, nullable=False))

ambient = sa.Table("ambient", metadata,
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), primary_key=True),
    _ts(), sa.Column("brief", JSONB, nullable=False, server_default="{}"))

firmware_releases = sa.Table("firmware_releases", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("device_type", sa.Text, nullable=False), sa.Column("channel", sa.Text, nullable=False, server_default="stable"),
    sa.Column("version", sa.Text, nullable=False), sa.Column("url", sa.Text, nullable=False), sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("size", sa.Integer), sa.Column("min_version", sa.Text), sa.Column("notes", sa.Text), _ts("created_at"),
    sa.UniqueConstraint("device_type", "channel", "version"))

plant_nodes = sa.Table("plant_nodes", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
    sa.Column("device_id", sa.Text, nullable=False, unique=True), sa.Column("name", sa.Text, nullable=False, server_default="PotBot"),
    sa.Column("token_hash", sa.Text), sa.Column("settings", JSONB, nullable=False, server_default="{}"), _ts("created_at"))

plant_readings = sa.Table("plant_readings", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("node_id", sa.BigInteger, sa.ForeignKey("plant_nodes.id", ondelete="CASCADE"), nullable=False, index=True),
    _ts(), sa.Column("moisture", sa.Float), sa.Column("soil_temp", sa.Float), sa.Column("air_temp", sa.Float),
    sa.Column("rh", sa.Float), sa.Column("lux", sa.Float), sa.Column("co2", sa.Float))

faces = sa.Table("faces", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("household_id", sa.BigInteger, sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
    sa.Column("label", sa.Text, nullable=False), sa.Column("embedding", JSONB, nullable=False), _ts("created_at"))

sessions = sa.Table("sessions", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    _ts("created_at"), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
