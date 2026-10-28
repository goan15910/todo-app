"""Wire the in-memory application."""

from __future__ import annotations

from todo_app.adapters.memory import InMemoryTaskRepository
from todo_app.application.service import TaskService


def in_memory_app() -> TaskService:
    return TaskService(InMemoryTaskRepository())
