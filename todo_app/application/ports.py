"""Storage the use cases need."""

from __future__ import annotations

from typing import Protocol

from todo_app.domain.task import Status, Task


class TaskRepository(Protocol):
    """Tasks stored by id.

    ``list`` returns every stored task when ``status`` is omitted, and
    only tasks in that status otherwise. Both results are ordered by id.
    """

    def add(self, task: Task) -> None:
        """Store a task that is not already stored."""

    def save(self, task: Task) -> None:
        """Replace the stored copy of a task that is already stored."""

    def get(self, task_id: int) -> Task | None:
        """Return a copy of the task, or None when the id is absent."""

    def list(self, status: Status | None = None) -> tuple[Task, ...]:
        """Return copies of the stored tasks, ordered by id."""
