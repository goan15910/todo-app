"""The human-owned bridge from the tracker's ports to IssueTracker.

``abstract`` and ``ACTION_MAP`` are the whole mapping. ``MAPPING.sha256``
pins this file. Review any change here: a weaker ``abstract`` makes trace
acceptance vacuous.

The view is what a unit of work leaves in the repository, read back through
the repository port. Titles collapse to "" or "text". Timestamps collapse
to "unset" or "set". Comments collapse to their count. Keys and user names
pass through, so a trace fixes the model's ``Keys`` and ``Users`` constants
to the ones it used. ``version`` is not in the view: the spec does not model
concurrency.

Each binding names one service function, the spec action it is, and how
its arguments become the action's parameters: the project key, the issue
number, a string argument (title or user), and a numeric argument (the
blocker). The action name never depends on the state, so there is no
fan-out: a refused call is recorded under the same action and checked as
disabled.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from tracker.domain.model import IssueRef, Project


@dataclass(frozen=True)
class IssueView:
    number: int
    title: str
    status: str
    assignee: str
    blocked_by: tuple[int, ...]
    comments: int
    started_at: str
    completed_at: str
    canceled_at: str


@dataclass(frozen=True)
class ProjectView:
    key: str
    issues: tuple[IssueView, ...]


@dataclass(frozen=True)
class View:
    projects: tuple[ProjectView, ...]


@dataclass(frozen=True)
class Label:
    """The spec action and parameters one service call stands for."""

    action: str
    key: str
    number: int = 0
    arg: str = ""
    num: int = 0


@dataclass(frozen=True)
class ActionBinding:
    method: str
    action: str
    key_param: str | None = None
    ref_param: str | None = None
    title_param: str | None = None
    user_param: str | None = None
    blocker_param: str | None = None

    def label(self, bound: inspect.BoundArguments) -> Label | None:
        """The label for these arguments, or None when a ref does not parse."""

        arguments = bound.arguments
        key = ""
        number = 0
        if self.key_param is not None:
            key = arguments[self.key_param]
            if not isinstance(key, str):
                return None
        if self.ref_param is not None:
            try:
                ref = IssueRef.parse(arguments[self.ref_param])
            except (TypeError, ValueError):
                return None
            key, number = ref.key, ref.number
        arg = ""
        num = 0
        if self.title_param is not None:
            title = arguments[self.title_param]
            if not isinstance(title, str):
                return None
            arg = abstract_title(title)
        if self.user_param is not None:
            user = arguments[self.user_param]
            if not isinstance(user, str):
                return None
            arg = user
        if self.blocker_param is not None:
            try:
                blocker = IssueRef.parse(arguments[self.blocker_param])
            except (TypeError, ValueError):
                return None
            if blocker.key != key:
                return None
            num = blocker.number
        return Label(self.action, key, number, arg, num)


ACTION_MAP: tuple[ActionBinding, ...] = (
    ActionBinding("create_project", "CreateProject", key_param="key"),
    ActionBinding("create_issue", "CreateIssue", key_param="key", title_param="title"),
    ActionBinding("triage", "Triage", ref_param="ref"),
    ActionBinding("assign", "Assign", ref_param="ref", user_param="user"),
    ActionBinding("unassign", "Unassign", ref_param="ref"),
    ActionBinding("start", "Start", ref_param="ref"),
    ActionBinding("submit_review", "SubmitReview", ref_param="ref"),
    ActionBinding("request_changes", "RequestChanges", ref_param="ref"),
    ActionBinding("approve", "Approve", ref_param="ref"),
    ActionBinding("cancel", "Cancel", ref_param="ref"),
    ActionBinding("reopen", "Reopen", ref_param="ref"),
    ActionBinding("add_blocker", "AddBlocker", ref_param="ref", blocker_param="blocker"),
    ActionBinding("remove_blocker", "RemoveBlocker", ref_param="ref", blocker_param="blocker"),
    ActionBinding("comment", "Comment", ref_param="ref"),
)


def action_names() -> tuple[str, ...]:
    names = tuple(binding.action for binding in ACTION_MAP)
    if len(names) != len(set(names)):
        raise RuntimeError("ACTION_MAP names the same spec action twice")
    return names


def binding_for(action: str) -> ActionBinding:
    for binding in ACTION_MAP:
        if binding.action == action:
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
    return "text"


def _mark(value: datetime | None) -> str:
    if value is None:
        return "unset"
    if isinstance(value, datetime):
        return "set"
    raise TypeError("time must be a datetime or None")


def abstract(projects: Iterable[Project]) -> View:
    """Project every stored project. The input is what the repository port lists."""

    views = []
    for project in projects:
        issues = tuple(
            IssueView(
                number=issue.number,
                title=abstract_title(issue.title),
                status=issue.status.value,
                assignee="unset" if issue.assignee is None else issue.assignee,
                blocked_by=tuple(sorted(issue.blocked_by)),
                comments=len(issue.comments),
                started_at=_mark(issue.started_at),
                completed_at=_mark(issue.completed_at),
                canceled_at=_mark(issue.canceled_at),
            )
            for issue in sorted(project.issues, key=lambda item: item.number)
        )
        views.append(ProjectView(key=project.key, issues=issues))
    views.sort(key=lambda item: item.key)
    return View(projects=tuple(views))
