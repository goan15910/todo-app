"""A unit of work over one SQLAlchemy connection and one transaction.

Enter opens a transaction. Commit writes back every project the repository
handed out, with the version check, then commits the transaction. Leaving
without commit, or any exception, rolls the transaction back, so the tables
never see a half-made change.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, Transaction, create_engine
from sqlalchemy.pool import StaticPool

from tracker.adapters.sqlalchemy import orm
from tracker.adapters.sqlalchemy.repository import SqlAlchemyRepository
from tracker.service_layer.unit_of_work import AbstractUnitOfWork


def sqlite_engine(url: str = "sqlite+pysqlite:///:memory:") -> Engine:
    """An engine with the schema created and foreign keys enforced."""

    kwargs: dict[str, object] = {"future": True}
    if url.endswith(":memory:"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)  # type: ignore[arg-type]

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    orm.metadata.create_all(engine)
    return engine


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.committed = False

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self._connection = self._engine.connect()
        self._transaction = self._connection.begin()
        self.projects = SqlAlchemyRepository(self._connection)
        self.committed = False
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        try:
            super().__exit__(exc_type, exc, traceback)
        finally:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._transaction = None

    def _commit(self) -> None:
        if self._transaction is None or self._connection is None:
            raise RuntimeError("commit outside the unit of work")
        for project in self.projects.seen.values():
            self.projects.persist(project)
        self._transaction.commit()
        self.committed = True
        self._transaction = self._connection.begin()
        self.projects = SqlAlchemyRepository(self._connection)

    def rollback(self) -> None:
        if self._transaction is None or self._connection is None:
            return
        self._transaction.rollback()
        self._transaction = self._connection.begin()
        self.projects = SqlAlchemyRepository(self._connection)
