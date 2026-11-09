"""Request and response bodies. Web shapes, not domain objects."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from tracker.domain.model import Comment, Issue, Project


class ProjectIn(BaseModel):
    key: str
    name: str


class IssueIn(BaseModel):
    title: str


class AssigneeIn(BaseModel):
    user: str


class BlockerIn(BaseModel):
    blocker: str


class CommentIn(BaseModel):
    body: str


class CommentOut(BaseModel):
    body: str
    created_at: datetime

    @classmethod
    def of(cls, comment: Comment) -> CommentOut:
        return cls(body=comment.body, created_at=comment.created_at)


class IssueOut(BaseModel):
    ref: str
    key: str
    number: int
    title: str
    status: str
    assignee: str | None
    blocked_by: list[str]
    comments: list[CommentOut]
    started_at: datetime | None
    completed_at: datetime | None
    canceled_at: datetime | None

    @classmethod
    def of(cls, issue: Issue) -> IssueOut:
        return cls(
            ref=str(issue.ref),
            key=issue.key,
            number=issue.number,
            title=issue.title,
            status=issue.status.value,
            assignee=issue.assignee,
            blocked_by=[f"{issue.key}-{blocker}" for blocker in sorted(issue.blocked_by)],
            comments=[CommentOut.of(comment) for comment in issue.comments],
            started_at=issue.started_at,
            completed_at=issue.completed_at,
            canceled_at=issue.canceled_at,
        )


class ProjectOut(BaseModel):
    key: str
    name: str
    version: int
    issue_count: int

    @classmethod
    def of(cls, project: Project) -> ProjectOut:
        return cls(
            key=project.key,
            name=project.name,
            version=project.version,
            issue_count=len(project.issues),
        )


class ErrorOut(BaseModel):
    detail: str
