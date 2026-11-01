"""The human-owned bridge from the repository to TodoTask.

``abstract`` and ``ACTION_MAP`` are the whole mapping. ``MAPPING.sha256``
pins this file. Review any change here: a weaker ``abstract`` makes trace
acceptance vacuous.

``submitted`` is not in the view. The chart has no such field, and it is
not a function of the stored task (a restored draft can be an unlisted
todo). The trace spec keeps it as auxiliary history by taking the model's
own actions.

``seq`` is derived from the stored ids, ``1`` when the repository is empty
and ``max(id) + 1`` otherwise. IdsDense is what makes that the model's
``seq``. The id port is not an input to this function.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TaskView:
    id: int
    state: str
    title: str
    listed: bool
    started_at: str
    completed_at: str
    wake_at: str
    deleted_at: str


@dataclass(frozen=True)
class View:
    seq: int
    tasks: tuple[TaskView, ...]


@dataclass(frozen=True)
class ActionBinding:
    """One use case, and the spec action or status-to-action fan-out it is."""

    method: str
    action: str | tuple[tuple[str, str], ...]
    id_param: str | None = None
    title_param: str | None = None

    def names(self) -> tuple[str, ...]:
        if isinstance(self.action, str):
            return (self.action,)
        return tuple(name for _status, name in self.action)

    def resolve(self, status: str | None) -> str | None:
        if isinstance(self.action, str):
            return self.action
        for item, name in self.action:
            if item == status:
                return name
        return None


ACTION_MAP: tuple[ActionBinding, ...] = (
    ActionBinding("add_task", "Add", title_param="title"),
    ActionBinding("submit", "DraftSubmit", id_param="task_id"),
    ActionBinding(
        "delete",
        (("draft", "DraftDelete"), ("todo", "TodoDelete")),
        id_param="task_id",
    ),
    ActionBinding("start", "TodoStart", id_param="task_id"),
    ActionBinding(
        "complete",
        (("todo", "TodoComplete"), ("inProgress", "InProgressComplete")),
        id_param="task_id",
    ),
    ActionBinding(
        "snooze",
        (("todo", "TodoSnooze"), ("inProgress", "InProgressSnooze")),
        id_param="task_id",
    ),
    ActionBinding("resume", "SnoozedResume", id_param="task_id"),
    ActionBinding("reopen", "DoneReopen", id_param="task_id"),
    ActionBinding("archive", "DoneArchive", id_param="task_id"),
    ActionBinding("unarchive", "ArchivedUnarchive", id_param="task_id"),
    ActionBinding("restore", "DeletedRestore", id_param="task_id"),
)


def action_names() -> tuple[str, ...]:
    names: list[str] = []
    for binding in ACTION_MAP:
        names.extend(binding.names())
    if len(names) != len(set(names)):
        raise RuntimeError("ACTION_MAP names the same spec action twice")
    return tuple(names)


def binding_for(action: str) -> ActionBinding:
    for binding in ACTION_MAP:
        if action in binding.names():
            return binding
    raise KeyError(action)


def binding_for_method(method: str) -> ActionBinding | None:
    for binding in ACTION_MAP:
        if binding.method == method:
            return binding
    return None


def abstract_title(title: str) -> str:
    if not isinstance(title, str):
        raise TypeError("title must be a string")
    if title == "":
        return ""
    return "task"


def _mark(value: datetime | None) -> str:
    if value is None:
        return "unset"
    if isinstance(value, datetime):
        return "set"
    raise TypeError("time must be a datetime or None")


def abstract(repository: Any) -> View:
    """Project stored tasks. Datetimes become set/unset. Titles become "" or "task"."""

    tasks = tuple(
        TaskView(
            id=task.id,
            state=task.status.value,
            title=abstract_title(task.title),
            listed=task.listed,
            started_at=_mark(task.started_at),
            completed_at=_mark(task.completed_at),
            wake_at=_mark(task.wake_at),
            deleted_at=_mark(task.deleted_at),
        )
        for task in repository.list()
    )
    tasks = tuple(sorted(tasks, key=lambda item: item.id))
    seq = 1 if not tasks else max(item.id for item in tasks) + 1
    return View(seq=seq, tasks=tasks)
