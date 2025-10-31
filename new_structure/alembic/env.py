from __future__ import annotations
import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# Load .env file
load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Adjust sys.path to include project parent so 'new_structure' package is importable
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, '..'))  # .../new_structure
PROJECT_PARENT = os.path.abspath(os.path.join(PROJECT_ROOT, '..'))  # .../phase1
for p in (PROJECT_PARENT,):
    if p not in sys.path:
        sys.path.insert(0, p)

try:  # Import db and models via package-qualified imports
    from new_structure.extensions import db  # noqa
    # Import the models package which imports all model modules
    from new_structure import models  # noqa
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed importing models for Alembic: {e}")

target_metadata = db.metadata

# Build Database URL from .env variables
from urllib.parse import quote_plus
MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
MYSQL_PORT = os.getenv('MYSQL_PORT', '3306')
MYSQL_USER = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', 'hillview_demo001')

# URL-encode password to handle special characters
_ENC_PWD = quote_plus(MYSQL_PASSWORD) if MYSQL_PASSWORD else ''
DB_URL = f"mysql+pymysql://{MYSQL_USER}:{_ENC_PWD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"

# Escape % for configparser interpolation handling
safe_url = DB_URL.replace('%', '%%')
config.set_main_option('sqlalchemy.url', safe_url)


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
