"""Failures the aggregate reports, named in the tracker's own words."""

from __future__ import annotations


class TrackerError(Exception):
    """Base of every failure a caller of the tracker may want to catch."""


class Rejected(TrackerError):
    """The issue's current status, or the project's state, forbids the action."""

    def __init__(self, ref: str, status: str, action: str) -> None:
        self.ref = ref
        self.status = status
        self.action = action
        super().__init__(f"{action} rejected for {ref} in {status}")


class IssueNotFound(TrackerError):
    """The project has no issue with this number."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"issue {ref} not found")


class InvalidTitle(TrackerError):
    """An issue needs a non-empty title."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"issue title in {key} must not be empty")


class InvalidAssignee(TrackerError):
    """An assignee is a non-empty user name."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"assignee for {ref} must not be empty")
