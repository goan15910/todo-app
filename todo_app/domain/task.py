"""A task and the rules for changing its status."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from todo_app.domain.errors import TaskRejected


class Status(Enum):
    DRAFT = "draft"
    TODO = "todo"
    IN_PROGRESS = "inProgress"
    SNOOZED = "snoozed"
    DONE = "done"
    ARCHIVED = "archived"
    DELETED = "deleted"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_int(task_id: object) -> int:
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        raise TypeError("task_id must be an int")
    return task_id


def _require_stamp(name: str, value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime or None")
    return value


class Task:
    """One task.

    A new task is an unlisted draft. Submitting it requires a title and
    lists it. Later steps stamp or clear the time that belongs to that
    step: start, completion, wake, and deletion. The title does not change.
    """

    __slots__ = (
        "_id",
        "_title",
        "_status",
        "_listed",
        "_started_at",
        "_completed_at",
        "_wake_at",
        "_deleted_at",
    )

    def __init__(
        self,
        task_id: int,
        title: str,
        status: Status,
        listed: bool,
        started_at: datetime | None,
        completed_at: datetime | None,
        wake_at: datetime | None,
        deleted_at: datetime | None,
    ) -> None:
        if not isinstance(title, str):
            raise TypeError("title must be a string")
        if not isinstance(status, Status):
            raise TypeError("status must be a Status")
        if not isinstance(listed, bool):
            raise TypeError("listed must be a bool")
        self._id = _require_int(task_id)
        self._title = title
        self._status = status
        self._listed = listed
        self._started_at = _require_stamp("started_at", started_at)
        self._completed_at = _require_stamp("completed_at", completed_at)
        self._wake_at = _require_stamp("wake_at", wake_at)
        self._deleted_at = _require_stamp("deleted_at", deleted_at)

    @classmethod
    def draft(cls, task_id: int, title: str) -> Task:
        return cls(task_id, title, Status.DRAFT, False, None, None, None, None)

    @classmethod
    def from_stored(
        cls,
        task_id: int,
        title: str,
        status: Status,
        listed: bool,
        started_at: datetime | None,
        completed_at: datetime | None,
        wake_at: datetime | None,
        deleted_at: datetime | None,
    ) -> Task:
        """Rebuild a task that was stored. Does not replay its history."""

        return cls(
            task_id,
            title,
            status,
            listed,
            started_at,
            completed_at,
            wake_at,
            deleted_at,
        )

    @property
    def id(self) -> int:
        return self._id

    @property
    def title(self) -> str:
        return self._title

    @property
    def status(self) -> Status:
        return self._status

    @property
    def listed(self) -> bool:
        return self._listed

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def completed_at(self) -> datetime | None:
        return self._completed_at

    @property
    def wake_at(self) -> datetime | None:
        return self._wake_at

    @property
    def deleted_at(self) -> datetime | None:
        return self._deleted_at

    def submit(self) -> None:
        if self._status is not Status.DRAFT:
            self._reject("submit")
        if self._title == "":
            self._reject("submit")
        self._status = Status.TODO
        self._listed = True

    def delete(self) -> None:
        if self._status is not Status.DRAFT and self._status is not Status.TODO:
            self._reject("delete")
        self._status = Status.DELETED
        self._deleted_at = _now()

    def start(self) -> None:
        if self._status is not Status.TODO:
            self._reject("start")
        self._status = Status.IN_PROGRESS
        self._started_at = _now()

    def complete(self) -> None:
        if self._status is not Status.TODO and self._status is not Status.IN_PROGRESS:
            self._reject("complete")
        self._status = Status.DONE
        self._completed_at = _now()

    def snooze(self, wake_at: datetime) -> None:
        if not isinstance(wake_at, datetime):
            raise TypeError("wake_at must be a datetime")
        if self._status is not Status.TODO and self._status is not Status.IN_PROGRESS:
            self._reject("snooze")
        self._status = Status.SNOOZED
        self._wake_at = wake_at

    def resume(self) -> None:
        if self._status is not Status.SNOOZED:
            self._reject("resume")
        self._status = Status.IN_PROGRESS
        self._wake_at = None

    def reopen(self) -> None:
        if self._status is not Status.DONE:
            self._reject("reopen")
        self._status = Status.TODO
        self._completed_at = None

    def archive(self) -> None:
        if self._status is not Status.DONE:
            self._reject("archive")
        if self._completed_at is None:
            self._reject("archive")
        self._status = Status.ARCHIVED
        self._listed = False

    def unarchive(self) -> None:
        if self._status is not Status.ARCHIVED:
            self._reject("unarchive")
        self._status = Status.DONE
        self._listed = True

    def restore(self) -> None:
        if self._status is not Status.DELETED:
            self._reject("restore")
        self._status = Status.TODO
        self._deleted_at = None

    def _reject(self, action: str) -> None:
        raise TaskRejected(self._id, self._status.value, action)

    def __repr__(self) -> str:
        return (
            f"Task(id={self._id!r}, status={self._status.value!r}, "
            f"title={self._title!r}, listed={self._listed!r})"
        )
