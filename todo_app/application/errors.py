"""Use-case failures."""

from __future__ import annotations


class TaskNotFound(Exception):
    """No stored task has this id."""

    def __init__(self, task_id: int) -> None:
        self.task_id = task_id
        super().__init__(f"task {task_id} not found")
