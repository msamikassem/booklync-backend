from logging.config import fileConfig
from sqlalchemy import create_engine
from sqlalchemy import pool
from alembic import context

# import your settings and models
from app.core.config import settings
from app.models.models import Base

# this is the Alembic Config object
config = context.config

# setup loggers
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# this is what tells Alembic which tables to track
target_metadata = Base.metadata

# Build the engine directly from settings so that special characters in the
# password (e.g. % signs from URL-encoding) are never passed through
# configparser, which would misinterpret them as interpolation syntax.
_connect_args = {"sslmode": "require"} if settings.DB_USE_SSL else {}


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        settings.DATABASE_URL,
        connect_args=_connect_args,
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()