"""Walk the b-full edges in :mod:`todo_app.task`.

The edges are the actions in ``spec/TodoTask.tla``. This module only calls
the task implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from todo_app.task import TodoTask, TransitionRejected

_WAKE = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def run_todo_flow(wake_at: datetime | None = None) -> None:
    """Exercise every chart edge, plus the empty-title submit rejection."""

    wake = wake_at or _WAKE
    _through_archive(wake)
    _complete_from_todo()
    _snooze_from_todo(wake)
    _delete_from_todo()
    _delete_draft()
    _complete_from_in_progress()
    _reject_empty_submit()


def _through_archive(wake: datetime) -> None:
    task = TodoTask("")
    task.set_title("task")
    task.submit()
    task.start()
    task.snooze(wake)
    task.resume()
    task.complete()
    task.reopen()
    task.complete()
    task.archive()
    task.unarchive()


def _complete_from_todo() -> None:
    task = TodoTask("task")
    task.submit()
    task.complete()


def _snooze_from_todo(wake: datetime) -> None:
    task = TodoTask("task")
    task.submit()
    task.snooze(wake)


def _delete_from_todo() -> None:
    task = TodoTask("task")
    task.submit()
    task.delete()
    task.restore()


def _delete_draft() -> None:
    TodoTask("").delete()


def _complete_from_in_progress() -> None:
    task = TodoTask("task")
    task.submit()
    task.start()
    task.complete()


def _reject_empty_submit() -> None:
    try:
        TodoTask("").submit()
    except TransitionRejected:
        return
    raise AssertionError("empty submit was accepted")
