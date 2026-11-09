"""Failures the service layer reports before or instead of the aggregate."""

from __future__ import annotations

from tracker.domain.errors import TrackerError


class ProjectNotFound(TrackerError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"project {key} not found")


class ProjectExists(TrackerError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"project {key} already exists")


class InvalidRef(TrackerError):
    """A malformed ref, a bad key, or a blocker from another project."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ConcurrencyConflict(TrackerError):
    """Another unit of work changed the project since it was loaded."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"project {key} was changed by another unit of work")
