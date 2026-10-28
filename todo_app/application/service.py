"""Task use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from todo_app.application.errors import TaskNotFound
from todo_app.application.ports import TaskRepository
from todo_app.domain.task import Status, Task


class TaskService:
    """Add, fetch, list, and move tasks. The repository stores them."""

    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository
        self._next_id = 1

    def add_task(self, title: str) -> Task:
        task = Task.draft(self._next_id, title)
        self._repository.add(task)
        self._next_id += 1
        return self.get_task(task.id)

    def get_task(self, task_id: int) -> Task:
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            raise TypeError("task_id must be an int")
        stored = self._repository.get(task_id)
        if stored is None:
            raise TaskNotFound(task_id)
        return stored

    def list_tasks(self, status: Status | None = None) -> tuple[Task, ...]:
        if status is not None and not isinstance(status, Status):
            raise TypeError("status must be a Status or None")
        return self._repository.list(status)

    def submit(self, task_id: int) -> Task:
        return self._persist(task_id, Task.submit)

    def delete(self, task_id: int) -> Task:
        return self._persist(task_id, Task.delete)

    def start(self, task_id: int) -> Task:
        return self._persist(task_id, Task.start)

    def complete(self, task_id: int) -> Task:
        return self._persist(task_id, Task.complete)

    def snooze(self, task_id: int, wake_at: datetime) -> Task:
        return self._persist(task_id, lambda task: task.snooze(wake_at))

    def resume(self, task_id: int) -> Task:
        return self._persist(task_id, Task.resume)

    def reopen(self, task_id: int) -> Task:
        return self._persist(task_id, Task.reopen)

    def archive(self, task_id: int) -> Task:
        return self._persist(task_id, Task.archive)

    def unarchive(self, task_id: int) -> Task:
        return self._persist(task_id, Task.unarchive)

    def restore(self, task_id: int) -> Task:
        return self._persist(task_id, Task.restore)

    def _persist(self, task_id: int, change: Callable[[Task], None]) -> Task:
        task = self.get_task(task_id)
        change(task)
        self._repository.save(task)
        return self.get_task(task_id)
