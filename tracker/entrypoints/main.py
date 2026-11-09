"""``uvicorn tracker.entrypoints.main:app`` serves a SQLite-backed tracker."""

from __future__ import annotations

import os

from tracker.bootstrap import sqlite
from tracker.entrypoints.fastapi_app import create_app

app = create_app(sqlite(os.environ.get("TRACKER_DATABASE_URL", "sqlite+pysqlite:///tracker.db")))
