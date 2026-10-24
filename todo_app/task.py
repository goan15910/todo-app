"""One-task flow from Clew's b-full chart.

Chart: https://github.com/goan15910/clew examples/todo/b-full.yaml
Spec: spec/TodoTask.tla
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple


class TransitionRejected(Exception):
    """The chart has no edge for this event, or a guard refused it."""

    def __init__(self, state: str, event: str, reason: str) -> None:
        self.state = state
        self.event = event
        self.reason = reason
        super().__init__(f"{event} rejected in {state}: {reason}")


class Edge(NamedTuple):
    source: str
    event: str
    target: str
    effect: str
    guard: str | None


# Chart order. RESUME's actor is "user or timer"; resume() serves both.
EDGES: tuple[Edge, ...] = (
    Edge("draft", "SUBMIT", "todo", "markListed", "titleNotEmpty"),
    Edge("draft", "DELETE", "deleted", "stampDeleted", None),
    Edge("todo", "START", "inProgress", "stampStarted", None),
    Edge("todo", "COMPLETE", "done", "stampCompleted", None),
    Edge("todo", "SNOOZE", "snoozed", "setWakeAt", None),
    Edge("todo", "DELETE", "deleted", "stampDeleted", None),
    Edge("inProgress", "SNOOZE", "snoozed", "setWakeAt", None),
    Edge("inProgress", "COMPLETE", "done", "stampCompleted", None),
    Edge("snoozed", "RESUME", "inProgress", "clearWakeAt", None),
    Edge("done", "REOPEN", "todo", "clearCompleted", None),
    Edge("done", "ARCHIVE", "archived", "clearListed", "completedAtSet"),
    Edge("archived", "UNARCHIVE", "done", "markListed", None),
    Edge("deleted", "RESTORE", "todo", "clearDeleted", None),
)


def _index(edges: tuple[Edge, ...]) -> dict[tuple[str, str], Edge]:
    index: dict[tuple[str, str], Edge] = {}
    for edge in edges:
        key = (edge.source, edge.event)
        if key in index:
            raise RuntimeError(f"duplicate event {edge.event} from {edge.source}")
        index[key] = edge
    return index


_BY_SOURCE_EVENT = _index(EDGES)


class TodoTask:
    """A task record. Fields persist across states; effects are the writes.

    A draft starts unlisted, with no timestamps. `set_title` is not a chart
    event: the spec chooses the draft title in Init, and the title is fixed
    once a chart event has fired.
    """

    def __init__(self, title: str = "") -> None:
        if not isinstance(title, str):
            raise TypeError("title must be a string")
        self.state = "draft"
        self.title = title
        self.listed = False
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.wake_at: datetime | None = None
        self.deleted_at: datetime | None = None

    def set_title(self, title: str) -> None:
        if not isinstance(title, str):
            raise TypeError("title must be a string")
        if self.state != "draft":
            raise TransitionRejected(self.state, "set_title", "title is fixed after the draft")
        self.title = title

    def submit(self) -> None:
        self._apply("SUBMIT")

    def delete(self) -> None:
        self._apply("DELETE")

    def start(self) -> None:
        self._apply("START")

    def complete(self) -> None:
        self._apply("COMPLETE")

    def snooze(self, wake_at: datetime) -> None:
        if not isinstance(wake_at, datetime):
            raise TypeError("wake_at must be a datetime")
        self._apply("SNOOZE", wake_at=wake_at)

    def resume(self) -> None:
        self._apply("RESUME")

    def reopen(self) -> None:
        self._apply("REOPEN")

    def archive(self) -> None:
        self._apply("ARCHIVE")

    def unarchive(self) -> None:
        self._apply("UNARCHIVE")

    def restore(self) -> None:
        self._apply("RESTORE")

    def _apply(self, event: str, wake_at: datetime | None = None) -> None:
        edge = _BY_SOURCE_EVENT.get((self.state, event))
        if edge is None:
            raise TransitionRejected(self.state, event, "no transition")
        if edge.guard is not None and not self._guard_holds(edge.guard):
            raise TransitionRejected(self.state, event, edge.guard)
        self._run_effect(edge.effect, wake_at)
        self.state = edge.target

    def _guard_holds(self, guard: str) -> bool:
        if guard == "titleNotEmpty":
            return self.title != ""
        if guard == "completedAtSet":
            return self.completed_at is not None
        raise RuntimeError(f"unknown guard {guard}")

    def _run_effect(self, effect: str, wake_at: datetime | None) -> None:
        if effect == "markListed":
            self.listed = True
        elif effect == "clearListed":
            self.listed = False
        elif effect == "stampStarted":
            self.started_at = datetime.now(timezone.utc)
        elif effect == "stampCompleted":
            self.completed_at = datetime.now(timezone.utc)
        elif effect == "clearCompleted":
            self.completed_at = None
        elif effect == "setWakeAt":
            if wake_at is None:
                raise TypeError("wake_at must be a datetime")
            self.wake_at = wake_at
        elif effect == "clearWakeAt":
            self.wake_at = None
        elif effect == "stampDeleted":
            self.deleted_at = now
        elif effect == "clearDeleted":
            self.deleted_at = None
        else:
            raise RuntimeError(f"unknown effect {effect}")

    def __repr__(self) -> str:
        return (
            f"TodoTask(state={self.state!r}, title={self.title!r}, "
            f"listed={self.listed!r})"
        )


class TodoList:
    """Tasks addressed by id. Each task runs the b-full flow on its own."""

    def __init__(self) -> None:
        self._tasks: dict[int, TodoTask] = {}
        self._next_id = 1

    def create(self, title: str = "") -> tuple[int, TodoTask]:
        task_id = self._next_id
        self._next_id += 1
        task = TodoTask(title)
        self._tasks[task_id] = task
        return task_id, task

    def get(self, task_id: int) -> TodoTask:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise KeyError(task_id) from None
