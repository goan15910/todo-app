"""In-memory adapters: a repository over a dict and a unit of work that really rolls back.

The unit of work hands out deep copies of the stored projects. ``commit``
writes those copies back; leaving without commit drops them. So a refused
command cannot leak a half-made change into the store, which is what the
SQLite adapter guarantees with a transaction.
"""

from __future__ import annotations

import copy

from tracker.domain.model import Project
from tracker.service_layer.unit_of_work import AbstractUnitOfWork


class FakeDatabase:
    """Everything the fake repositories of one application share."""

    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}


class FakeRepository:
    def __init__(self, projects: dict[str, Project]) -> None:
        self._projects = projects

    def add(self, project: Project) -> None:
        if project.key in self._projects:
            raise ValueError(f"project {project.key} already exists")
        self._projects[project.key] = project

    def get(self, key: str) -> Project | None:
        return self._projects.get(key)

    def list(self) -> tuple[Project, ...]:
        return tuple(self._projects[key] for key in sorted(self._projects))


class FakeUnitOfWork(AbstractUnitOfWork):
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database
        self._working: dict[str, Project] | None = None
        self.committed = False
        self.projects = FakeRepository({})

    def __enter__(self) -> FakeUnitOfWork:
        self._working = copy.deepcopy(self._database.projects)
        self.projects = FakeRepository(self._working)
        self.committed = False
        return self

    def _commit(self) -> None:
        if self._working is None:
            raise RuntimeError("commit outside the unit of work")
        self._database.projects = copy.deepcopy(self._working)
        self.committed = True

    def rollback(self) -> None:
        if self._working is None:
            return
        self._working = copy.deepcopy(self._database.projects)
        self.projects = FakeRepository(self._working)
