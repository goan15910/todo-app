"""The project aggregate and the issues it owns.

A project is the consistency boundary. It numbers its issues densely from
1, and every rule about an issue that reads another issue (blockers, cycles,
approval) reads only issues of the same project. Issues are entities inside
the aggregate; comments are value objects on an issue. Nothing here knows
how it is stored or how it is called.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from tracker.domain.errors import InvalidAssignee, InvalidTitle, IssueNotFound, Rejected


class Status(Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "inProgress"
    IN_REVIEW = "inReview"
    DONE = "done"
    CANCELED = "canceled"


OPEN = frozenset({Status.BACKLOG, Status.TODO, Status.IN_PROGRESS, Status.IN_REVIEW})
CLOSED = frozenset({Status.DONE, Status.CANCELED})
ACTIVE = frozenset({Status.IN_PROGRESS, Status.IN_REVIEW})

_KEY = re.compile(r"[A-Z][A-Z0-9]{1,9}")
_REF = re.compile(r"([A-Z][A-Z0-9]{1,9})-([1-9][0-9]*)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def valid_key(key: object) -> bool:
    return isinstance(key, str) and _KEY.fullmatch(key) is not None


@dataclass(frozen=True)
class IssueRef:
    """The public identity of an issue, such as ENG-1."""

    key: str
    number: int

    @classmethod
    def parse(cls, text: str) -> IssueRef:
        if not isinstance(text, str):
            raise TypeError("issue ref must be a string")
        match = _REF.fullmatch(text)
        if match is None:
            raise ValueError(f"{text!r} is not an issue ref like ENG-1")
        return cls(match.group(1), int(match.group(2)))

    def __str__(self) -> str:
        return f"{self.key}-{self.number}"


@dataclass(frozen=True)
class Comment:
    """What someone said about an issue. Immutable once written."""

    body: str
    created_at: datetime


class Issue:
    """One issue of a project. Only the project changes it."""

    __slots__ = (
        "key",
        "number",
        "title",
        "status",
        "assignee",
        "blocked_by",
        "comments",
        "started_at",
        "completed_at",
        "canceled_at",
    )

    def __init__(
        self,
        key: str,
        number: int,
        title: str,
        status: Status = Status.BACKLOG,
        assignee: str | None = None,
        blocked_by: frozenset[int] = frozenset(),
        comments: tuple[Comment, ...] = (),
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        canceled_at: datetime | None = None,
    ) -> None:
        self.key = key
        self.number = number
        self.title = title
        self.status = status
        self.assignee = assignee
        self.blocked_by = frozenset(blocked_by)
        self.comments = tuple(comments)
        self.started_at = started_at
        self.completed_at = completed_at
        self.canceled_at = canceled_at

    @property
    def ref(self) -> IssueRef:
        return IssueRef(self.key, self.number)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Issue):
            return NotImplemented
        return self.ref == other.ref

    def __hash__(self) -> int:
        return hash(self.ref)

    def __repr__(self) -> str:
        return f"Issue({str(self.ref)!r}, status={self.status.value!r}, title={self.title!r})"

    def _reject(self, action: str) -> None:
        raise Rejected(str(self.ref), self.status.value, action)


class Project:
    """The aggregate root. Holds its issues and enforces every rule about them."""

    def __init__(
        self,
        key: str,
        name: str,
        issues: tuple[Issue, ...] = (),
        version: int = 0,
    ) -> None:
        if not valid_key(key):
            raise ValueError(f"{key!r} is not a project key like ENG")
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        self.key = key
        self.name = name
        self.version = version
        self._issues: dict[int, Issue] = {}
        for issue in issues:
            if issue.key != key or issue.number in self._issues:
                raise ValueError(f"{issue!r} does not belong to {key}")
            self._issues[issue.number] = issue
        if set(self._issues) != set(range(1, len(self._issues) + 1)):
            raise ValueError(f"issue numbers of {key} are not dense")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Project):
            return NotImplemented
        return self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    def __repr__(self) -> str:
        return f"Project({self.key!r}, issues={len(self._issues)})"

    @property
    def issues(self) -> tuple[Issue, ...]:
        return tuple(self._issues[number] for number in sorted(self._issues))

    @property
    def next_number(self) -> int:
        return len(self._issues) + 1

    def issue(self, number: int) -> Issue:
        if isinstance(number, bool) or not isinstance(number, int):
            raise TypeError("issue number must be an int")
        found = self._issues.get(number)
        if found is None:
            raise IssueNotFound(f"{self.key}-{number}")
        return found

    def create_issue(self, title: str) -> Issue:
        if not isinstance(title, str):
            raise TypeError("title must be a string")
        if title == "":
            raise InvalidTitle(self.key)
        issue = Issue(self.key, self.next_number, title)
        self._issues[issue.number] = issue
        return issue

    def triage(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.status is not Status.BACKLOG:
            issue._reject("triage")
        issue.status = Status.TODO
        return issue

    def assign(self, number: int, user: str) -> Issue:
        issue = self.issue(number)
        if not isinstance(user, str) or user == "":
            raise InvalidAssignee(str(issue.ref))
        if issue.status not in OPEN:
            issue._reject("assign")
        if issue.assignee == user:
            issue._reject("assign")
        issue.assignee = user
        return issue

    def unassign(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.assignee is None:
            issue._reject("unassign")
        if issue.status in ACTIVE:
            issue._reject("unassign")
        issue.assignee = None
        return issue

    def start(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.status is not Status.TODO:
            issue._reject("start")
        if issue.assignee is None:
            issue._reject("start")
        issue.status = Status.IN_PROGRESS
        issue.started_at = _now()
        return issue

    def submit_review(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.status is not Status.IN_PROGRESS:
            issue._reject("submit_review")
        issue.status = Status.IN_REVIEW
        return issue

    def request_changes(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.status is not Status.IN_REVIEW:
            issue._reject("request_changes")
        issue.status = Status.IN_PROGRESS
        return issue

    def approve(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.status is not Status.IN_REVIEW:
            issue._reject("approve")
        for blocker in issue.blocked_by:
            if self._issues[blocker].status not in CLOSED:
                issue._reject("approve")
        issue.status = Status.DONE
        issue.completed_at = _now()
        return issue

    def cancel(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.status not in OPEN:
            issue._reject("cancel")
        issue.status = Status.CANCELED
        issue.canceled_at = _now()
        return issue

    def reopen(self, number: int) -> Issue:
        issue = self.issue(number)
        if issue.status not in CLOSED:
            issue._reject("reopen")
        issue.status = Status.TODO
        issue.completed_at = None
        issue.canceled_at = None
        return issue

    def add_blocker(self, number: int, blocker: int) -> Issue:
        issue = self.issue(number)
        other = self.issue(blocker)
        if other is issue:
            issue._reject("add_blocker")
        if issue.status not in OPEN:
            issue._reject("add_blocker")
        if blocker in issue.blocked_by:
            issue._reject("add_blocker")
        if number in self.transitive_blockers(blocker):
            issue._reject("add_blocker")
        issue.blocked_by = issue.blocked_by | {blocker}
        return issue

    def remove_blocker(self, number: int, blocker: int) -> Issue:
        issue = self.issue(number)
        if blocker not in issue.blocked_by:
            issue._reject("remove_blocker")
        issue.blocked_by = issue.blocked_by - {blocker}
        return issue

    def comment(self, number: int, body: str) -> Comment:
        issue = self.issue(number)
        if not isinstance(body, str):
            raise TypeError("comment body must be a string")
        if issue.status is Status.CANCELED:
            issue._reject("comment")
        written = Comment(body, _now())
        issue.comments = issue.comments + (written,)
        return written

    def transitive_blockers(self, number: int) -> frozenset[int]:
        """Every issue that blocks ``number``, directly or through other blockers."""

        seen: set[int] = set()
        frontier = set(self.issue(number).blocked_by)
        while frontier:
            seen |= frontier
            frontier = {
                upstream
                for blocker in frontier
                for upstream in self._issues[blocker].blocked_by
            } - seen
        return frozenset(seen)
