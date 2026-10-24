"""Alignment of one ``TodoTask`` onto ``spec/TodoTask.tla``.

Edit ``_METHODS``, ``ACTIONS``, ``project_task``, and ``advance_task`` to
change the correspondence. ``set_title`` is not a step: the spec chooses
the draft title in ``Init``. A non-empty title is the spec value ``"task"``.
Timestamps are ``"unset"`` or ``"set"``. ``submitted`` is not a field of
the task; ``advance_task`` remembers it.
"""

from __future__ import annotations

from dataclasses import dataclass

from event_alignment import Alignment
from todo_app.task import TodoTask

_METHODS = {
    "submit": "SUBMIT",
    "delete": "DELETE",
    "start": "START",
    "complete": "COMPLETE",
    "snooze": "SNOOZE",
    "resume": "RESUME",
    "reopen": "REOPEN",
    "archive": "ARCHIVE",
    "unarchive": "UNARCHIVE",
    "restore": "RESTORE",
}

ACTIONS = {
    ("draft", "SUBMIT"): "DraftSubmit",
    ("draft", "DELETE"): "DraftDelete",
    ("todo", "START"): "TodoStart",
    ("todo", "COMPLETE"): "TodoComplete",
    ("todo", "SNOOZE"): "TodoSnooze",
    ("todo", "DELETE"): "TodoDelete",
    ("inProgress", "SNOOZE"): "InProgressSnooze",
    ("inProgress", "COMPLETE"): "InProgressComplete",
    ("snoozed", "RESUME"): "SnoozedResume",
    ("done", "REOPEN"): "DoneReopen",
    ("done", "ARCHIVE"): "DoneArchive",
    ("archived", "UNARCHIVE"): "ArchivedUnarchive",
    ("deleted", "RESTORE"): "DeletedRestore",
}


@dataclass(frozen=True, slots=True)
class TaskHistory:
    submitted: bool = False


def project_task(task: TodoTask, history: object) -> dict[str, object]:
    if not isinstance(history, TaskHistory):
        raise TypeError("history must be TaskHistory")

    def mark(value: object) -> str:
        return "unset" if value is None else "set"

    title = task.title
    return {
        "state": task.state,
        "title": "" if title == "" else "task",
        "listed": task.listed,
        "started_at": mark(task.started_at),
        "completed_at": mark(task.completed_at),
        "wake_at": mark(task.wake_at),
        "deleted_at": mark(task.deleted_at),
        "submitted": history.submitted,
    }


def advance_task(history: object, action: str) -> TaskHistory:
    if not isinstance(history, TaskHistory):
        raise TypeError("history must be TaskHistory")
    if action == "DraftSubmit":
        return TaskHistory(submitted=True)
    return history


todo_alignment = Alignment(
    variable="task",
    steps={f"TodoTask.{name}": event for name, event in _METHODS.items()},
    actions=ACTIONS,
    project=project_task,
    advance=advance_task,
    initial_history=TaskHistory(),
)
