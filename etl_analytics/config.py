"""Explicit ETL settings, independent of API imports and JWT configuration."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def engines(demo_database=None):
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    source = os.getenv('ETL_SOURCE_URL') or os.getenv('DATABASE_URL')
    target = os.getenv('ETL_TARGET_URL') or source
    if not source or not target:
        raise ValueError('Set DATABASE_URL or ETL_SOURCE_URL and ETL_TARGET_URL')
    if any(make_url(url).get_backend_name() != 'postgresql' for url in (source, target)):
        raise ValueError('ETL requires PostgreSQL')
    if demo_database is not None:
        if not re.fullmatch(r'etl_demo_[a-f0-9]{12}', demo_database):
            raise ValueError('invalid_demo_database_name')
        source = make_url(source).set(database=demo_database)
        # Demo publication belongs to the demo database, even if a target URL exists.
        target = source
    options = {'connect_timeout': 5, 'options': '-cstatement_timeout=120000 -clock_timeout=3000'}
    return tuple(create_engine(url, hide_parameters=True, connect_args=options) for url in (source, target))
