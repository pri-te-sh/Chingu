import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alembic import context
from sqlalchemy import engine_from_config, pool
from pixel.models import metadata

config = context.config
url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url")).replace("+asyncpg", "+psycopg")
config.set_main_option("sqlalchemy.url", url)
target_metadata = metadata


def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
