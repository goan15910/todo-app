"""Composition roots. Each returns a factory of units of work."""

from __future__ import annotations

from collections.abc import Callable

from tracker.adapters.memory import FakeDatabase, FakeUnitOfWork
from tracker.service_layer.unit_of_work import AbstractUnitOfWork

UnitOfWorkFactory = Callable[[], AbstractUnitOfWork]


def in_memory() -> UnitOfWorkFactory:
    database = FakeDatabase()
    return lambda: FakeUnitOfWork(database)


def sqlite(url: str = "sqlite+pysqlite:///:memory:") -> UnitOfWorkFactory:
    from tracker.adapters.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork, sqlite_engine

    engine = sqlite_engine(url)
    return lambda: SqlAlchemyUnitOfWork(engine)
