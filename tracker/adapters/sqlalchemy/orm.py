"""The schema. It depends on nothing in the domain; the repository translates.

The project aggregate is reconstituted from these tables and written back
by the unit of work on commit. The schema may therefore differ from the
model: blockers and comments are rows here and a frozenset and a tuple
there.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    TypeDecorator,
)


class UTCDateTime(TypeDecorator):
    """Store naive UTC; hand back an aware UTC datetime. SQLite drops offsets."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("key", String(10), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("version", Integer, nullable=False),
)

issues = Table(
    "issues",
    metadata,
    Column("project_key", String(10), ForeignKey("projects.key"), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column("title", String(500), nullable=False),
    Column("status", String(20), nullable=False),
    Column("assignee", String(100), nullable=True),
    Column("started_at", UTCDateTime, nullable=True),
    Column("completed_at", UTCDateTime, nullable=True),
    Column("canceled_at", UTCDateTime, nullable=True),
)

blockers = Table(
    "blockers",
    metadata,
    Column("project_key", String(10), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column("blocker", Integer, primary_key=True),
    ForeignKeyConstraint(["project_key", "number"], ["issues.project_key", "issues.number"]),
    ForeignKeyConstraint(["project_key", "blocker"], ["issues.project_key", "issues.number"]),
)

comments = Table(
    "comments",
    metadata,
    Column("project_key", String(10), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("body", String(2000), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    ForeignKeyConstraint(["project_key", "number"], ["issues.project_key", "issues.number"]),
)
