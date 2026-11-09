"""Composition roots. Each returns a factory of units of work."""

from __future__ import annotations

from collections.abc import Callable

from tracker.adapters.memory import FakeDatabase, FakeUnitOfWork
from tracker.service_layer.unit_of_work import AbstractUnitOfWork

UnitOfWorkFactory = Callable[[], AbstractUnitOfWork]


def in_memory() -> UnitOfWorkFactory:
    database = FakeDatabase()
    return lambda: FakeUnitOfWork(database)
