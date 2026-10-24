"""Alignment of one ``TodoTask`` onto ``spec/TodoTask.tla``.

Edit ``_METHODS``, ``ACTIONS``, ``project_task``, ``advance_task``, and
``effect_task`` to change the correspondence. ``effect_task`` is how a
JSON Lines log is replayed: the log has no spec fields. ``set_title`` is
not a step: the spec chooses
the draft title in ``Init``. A non-empty title is the spec value ``"task"``.
Timestamps are ``"unset"`` or ``"set"``. ``submitted`` is not a field of
the task; ``advance_task`` remembers it.
"""

from __future__ import annotations

from dataclasses import dataclass

from collections.abc import Mapping

from event_alignment import Alignment, AlignmentError
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


def effect_task(fields: Mapping[str, object], action: str) -> dict[str, object]:
    """Successor record for ``action``. Matches the assignments in the spec."""

    updated = dict(fields)
    if action == "DraftSubmit":
        updated["state"] = "todo"
        updated["listed"] = True
        updated["submitted"] = True
    elif action == "DraftDelete":
        updated["state"] = "deleted"
        updated["deleted_at"] = "set"
    elif action == "TodoStart":
        updated["state"] = "inProgress"
        updated["started_at"] = "set"
    elif action == "TodoComplete":
        updated["state"] = "done"
        updated["completed_at"] = "set"
    elif action == "TodoSnooze":
        updated["state"] = "snoozed"
        updated["wake_at"] = "set"
    elif action == "TodoDelete":
        updated["state"] = "deleted"
        updated["deleted_at"] = "set"
    elif action == "InProgressSnooze":
        updated["state"] = "snoozed"
        updated["wake_at"] = "set"
    elif action == "InProgressComplete":
        updated["state"] = "done"
        updated["completed_at"] = "set"
    elif action == "SnoozedResume":
        updated["state"] = "inProgress"
        updated["wake_at"] = "unset"
    elif action == "DoneReopen":
        updated["state"] = "todo"
        updated["completed_at"] = "unset"
    elif action == "DoneArchive":
        updated["state"] = "archived"
        updated["listed"] = False
    elif action == "ArchivedUnarchive":
        updated["state"] = "done"
        updated["listed"] = True
    elif action == "DeletedRestore":
        updated["state"] = "todo"
        updated["deleted_at"] = "unset"
    else:
        raise AlignmentError(f"no effect for {action}")
    return updated


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
    effect=effect_task,
)
