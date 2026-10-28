"""Lifecycle failures."""

from __future__ import annotations


class TaskRejected(Exception):
    """This status does not allow the action."""

    def __init__(self, task_id: int, status: str, action: str) -> None:
        self.task_id = task_id
        self.status = status
        self.action = action
        super().__init__(f"{action} rejected in {status}")
