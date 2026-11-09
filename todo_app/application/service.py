"""Task use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from proof.registry import bind
from todo_app.application.errors import TaskNotFound
from todo_app.application.ports import IdAllocator, TaskRepository
from todo_app.domain.errors import TaskRejected
from todo_app.domain.task import Status, Task


class TaskService:
    """Add, fetch, list, and move tasks. The repository stores them."""

    def __init__(self, repository: TaskRepository, ids: IdAllocator) -> None:
        self._repository = repository
        self._ids = ids

    def add_task(self, title: str) -> Task:
        task = Task.draft(self._ids.peek(), title)
        task_id = self._ids.allocate()
        self._repository.add(task)
        return self._load(task_id)

    def get_task(self, task_id: int) -> Task:
        return self._load(task_id)

    def list_tasks(self, status: Status | None = None) -> tuple[Task, ...]:
        if status is not None and not isinstance(status, Status):
            raise TypeError("status must be a Status or None")
        return self._repository.list(status)

    def submit(self, task_id: int) -> Task:
        return self._apply(task_id, Task.submit)

    def delete(self, task_id: int) -> Task:
        task = self._load(task_id)
        if task.status is Status.DRAFT:
            task.delete_draft()
        elif task.status is Status.TODO:
            task.delete_todo()
        else:
            raise TaskRejected(task.id, task.status.value, "delete")
        return self._commit(task)

    def start(self, task_id: int) -> Task:
        return self._apply(task_id, Task.start)

    def complete(self, task_id: int) -> Task:
        task = self._load(task_id)
        if task.status is Status.TODO:
            task.complete_todo()
        elif task.status is Status.IN_PROGRESS:
            task.complete_in_progress()
        else:
            raise TaskRejected(task.id, task.status.value, "complete")
        return self._commit(task)

    def snooze(self, task_id: int, wake_at: datetime) -> Task:
        task = self._load(task_id)
        if task.status is Status.TODO:
            task.snooze_todo(wake_at)
        elif task.status is Status.IN_PROGRESS:
            task.snooze_in_progress(wake_at)
        else:
            raise TaskRejected(task.id, task.status.value, "snooze")
        return self._commit(task)

    def resume(self, task_id: int) -> Task:
        return self._apply(task_id, Task.resume)

    def reopen(self, task_id: int) -> Task:
        return self._apply(task_id, Task.reopen)

    def archive(self, task_id: int) -> Task:
        return self._apply(task_id, Task.archive)

    def unarchive(self, task_id: int) -> Task:
        return self._apply(task_id, Task.unarchive)

    def restore(self, task_id: int) -> Task:
        return self._apply(task_id, Task.restore)

    def _apply(self, task_id: int, change: Callable[[Task], None]) -> Task:
        task = self._load(task_id)
        change(task)
        return self._commit(task)

    def _commit(self, task: Task) -> Task:
        self._repository.save(task)
        return self._load(task.id)

    def _load(self, task_id: int) -> Task:
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            raise TypeError("task_id must be an int")
        stored = self._repository.get(task_id)
        if stored is None:
            raise TaskNotFound(task_id)
        return stored


bind(TaskService)
