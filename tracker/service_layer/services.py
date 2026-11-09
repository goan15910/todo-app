"""Use cases. One function per spec action, primitive arguments, one unit of work each.

Each function loads one project through the unit of work, calls the
aggregate, and commits. A refused command raises before commit, so the unit
of work rolls back and the repository is unchanged. Reads do not commit.
"""

from __future__ import annotations

from collections.abc import Callable

from tracker.domain.model import Comment, Issue, IssueRef, Project, Status, valid_key
from tracker.service_layer.errors import InvalidRef, ProjectExists, ProjectNotFound
from tracker.service_layer.unit_of_work import AbstractUnitOfWork


def create_project(key: str, name: str, uow: AbstractUnitOfWork) -> Project:
    if not valid_key(key):
        raise InvalidRef(f"{key!r} is not a project key like ENG")
    with uow:
        if uow.projects.get(key) is not None:
            raise ProjectExists(key)
        project = Project(key, name)
        uow.projects.add(project)
        uow.commit()
        return project


def create_issue(key: str, title: str, uow: AbstractUnitOfWork) -> Issue:
    if not valid_key(key):
        raise InvalidRef(f"{key!r} is not a project key like ENG")
    with uow:
        project = _project(uow, key)
        issue = project.create_issue(title)
        uow.commit()
        return issue


def triage(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.triage(number))


def assign(ref: str, user: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.assign(number, user))


def unassign(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.unassign(number))


def start(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.start(number))


def submit_review(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.submit_review(number))


def request_changes(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.request_changes(number))


def approve(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.approve(number))


def cancel(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.cancel(number))


def reopen(ref: str, uow: AbstractUnitOfWork) -> Issue:
    return _change(ref, uow, lambda project, number: project.reopen(number))


def add_blocker(ref: str, blocker: str, uow: AbstractUnitOfWork) -> Issue:
    other = _same_project(ref, blocker)
    return _change(ref, uow, lambda project, number: project.add_blocker(number, other))


def remove_blocker(ref: str, blocker: str, uow: AbstractUnitOfWork) -> Issue:
    other = _same_project(ref, blocker)
    return _change(ref, uow, lambda project, number: project.remove_blocker(number, other))


def comment(ref: str, body: str, uow: AbstractUnitOfWork) -> Comment:
    parsed = parse_ref(ref)
    with uow:
        project = _project(uow, parsed.key)
        written = project.comment(parsed.number, body)
        uow.commit()
        return written


def get_project(key: str, uow: AbstractUnitOfWork) -> Project:
    with uow:
        return _project(uow, key)


def list_projects(uow: AbstractUnitOfWork) -> tuple[Project, ...]:
    with uow:
        return uow.projects.list()


def get_issue(ref: str, uow: AbstractUnitOfWork) -> Issue:
    parsed = parse_ref(ref)
    with uow:
        return _project(uow, parsed.key).issue(parsed.number)


def list_issues(key: str, uow: AbstractUnitOfWork, status: Status | None = None) -> tuple[Issue, ...]:
    if status is not None and not isinstance(status, Status):
        raise TypeError("status must be a Status or None")
    with uow:
        issues = _project(uow, key).issues
    if status is None:
        return issues
    return tuple(issue for issue in issues if issue.status is status)


def parse_ref(ref: str) -> IssueRef:
    try:
        return IssueRef.parse(ref)
    except (TypeError, ValueError) as error:
        raise InvalidRef(str(error)) from error


def _same_project(ref: str, blocker: str) -> int:
    parsed = parse_ref(ref)
    other = parse_ref(blocker)
    if other.key != parsed.key:
        raise InvalidRef(f"{other} is not in project {parsed.key}")
    return other.number


def _change(ref: str, uow: AbstractUnitOfWork, change: Callable[[Project, int], Issue]) -> Issue:
    parsed = parse_ref(ref)
    with uow:
        project = _project(uow, parsed.key)
        issue = change(project, parsed.number)
        uow.commit()
        return issue


def _project(uow: AbstractUnitOfWork, key: str) -> Project:
    project = uow.projects.get(key)
    if project is None:
        raise ProjectNotFound(key)
    return project
