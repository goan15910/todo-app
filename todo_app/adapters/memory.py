"""In-memory task storage."""

from __future__ import annotations

from datetime import datetime

from todo_app.domain.task import Status, Task


class _Row:
    """One stored copy of a task. Not ordered, so a list must sort by id."""

    __slots__ = (
        "task_id",
        "title",
        "status",
        "listed",
        "started_at",
        "completed_at",
        "wake_at",
        "deleted_at",
    )

    def __init__(
        self,
        task_id: int,
        title: str,
        status: Status,
        listed: bool,
        started_at: datetime | None,
        completed_at: datetime | None,
        wake_at: datetime | None,
        deleted_at: datetime | None,
    ) -> None:
        self.task_id = task_id
        self.title = title
        self.status = status
        self.listed = listed
        self.started_at = started_at
        self.completed_at = completed_at
        self.wake_at = wake_at
        self.deleted_at = deleted_at


def _row_from(task: Task) -> _Row:
    return _Row(
        task_id=task.id,
        title=task.title,
        status=task.status,
        listed=task.listed,
        started_at=task.started_at,
        completed_at=task.completed_at,
        wake_at=task.wake_at,
        deleted_at=task.deleted_at,
    )


def _task_from(row: _Row) -> Task:
    return Task.from_stored(
        row.task_id,
        row.title,
        row.status,
        row.listed,
        row.started_at,
        row.completed_at,
        row.wake_at,
        row.deleted_at,
    )


class InMemoryTaskRepository:
    """A process-local repository. Stored rows are not the caller's objects."""

    def __init__(self) -> None:
        self._rows: dict[int, _Row] = {}

    def add(self, task: Task) -> None:
        if task.id in self._rows:
            raise ValueError(f"task {task.id} already exists")
        self._rows[task.id] = _row_from(task)

    def save(self, task: Task) -> None:
        if task.id not in self._rows:
            raise ValueError(f"task {task.id} is not stored")
        self._rows[task.id] = _row_from(task)

    def get(self, task_id: int) -> Task | None:
        row = self._rows.get(task_id)
        if row is None:
            return None
        return _task_from(row)

    def list(self, status: Status | None = None) -> tuple[Task, ...]:
        rows = list(self._rows.values())
        if status is not None:
            rows = [row for row in rows if row.status is status]
        rows.sort(key=lambda row: row.task_id)
        return tuple(_task_from(row) for row in rows)
