"""The ports: a repository of projects and the unit of work that owns it.

``spec/IssueTracker.tla`` is the contract of these ports. Its one variable
is what a committed unit of work leaves in the repository; its actions are
the service functions in ``services.py``.
"""

from __future__ import annotations

import abc
from types import TracebackType
from typing import Protocol

from tracker.domain.model import Project


class AbstractRepository(Protocol):
    """Projects by key. Aggregates only; there is no update method.

    Changes to a project fetched inside a unit of work are persisted by that
    unit of work's commit, not by a repository call.
    """

    def add(self, project: Project) -> None:
        """Store a project whose key is not stored yet."""

    def get(self, key: str) -> Project | None:
        """Return the project with this key, or None."""

    def list(self) -> tuple[Project, ...]:
        """Return every stored project, ordered by key."""


class AbstractUnitOfWork(abc.ABC):
    """One atomic operation. Leaving without ``commit`` rolls back."""

    projects: AbstractRepository

    def __enter__(self) -> AbstractUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.rollback()

    def commit(self) -> None:
        self._commit()

    @abc.abstractmethod
    def _commit(self) -> None:
        """Persist everything changed since ``__enter__``."""

    @abc.abstractmethod
    def rollback(self) -> None:
        """Discard everything not yet committed. Safe to call twice."""
