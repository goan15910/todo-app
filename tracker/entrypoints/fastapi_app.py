"""The HTTP entrypoint. It parses, calls one service function, and serialises.

``create_app`` takes the unit-of-work factory and, optionally, the object
whose attributes are the service functions. Production passes the
``services`` module. The proof passes a recording facade with the same
names, so the app never knows whether it is being proved.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from tracker.bootstrap import UnitOfWorkFactory
from tracker.domain.errors import InvalidAssignee, InvalidTitle, IssueNotFound, Rejected
from tracker.domain.model import Status
from tracker.entrypoints import schemas
from tracker.service_layer import services as default_services
from tracker.service_layer.errors import (
    ConcurrencyConflict,
    InvalidRef,
    ProjectExists,
    ProjectNotFound,
)

TRANSITIONS = {
    "triage": "triage",
    "start": "start",
    "submit-review": "submit_review",
    "request-changes": "request_changes",
    "approve": "approve",
    "cancel": "cancel",
    "reopen": "reopen",
}

_STATUS_OF = {
    ProjectNotFound: status.HTTP_404_NOT_FOUND,
    IssueNotFound: status.HTTP_404_NOT_FOUND,
    Rejected: status.HTTP_409_CONFLICT,
    ProjectExists: status.HTTP_409_CONFLICT,
    InvalidTitle: status.HTTP_409_CONFLICT,
    InvalidAssignee: status.HTTP_409_CONFLICT,
    ConcurrencyConflict: status.HTTP_409_CONFLICT,
    InvalidRef: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def create_app(uow_factory: UnitOfWorkFactory, services: ModuleType | Any = default_services) -> FastAPI:
    app = FastAPI(title="tracker", version="0.1")
    errors = {404: {"model": schemas.ErrorOut}, 409: {"model": schemas.ErrorOut}}

    for error_type, code in _STATUS_OF.items():

        def handle(_request: Request, error: Exception, code: int = code) -> JSONResponse:
            return JSONResponse(status_code=code, content={"detail": str(error)})

        app.add_exception_handler(error_type, handle)

    @app.post("/projects", status_code=201, response_model=schemas.ProjectOut, responses=errors)
    def create_project(body: schemas.ProjectIn) -> schemas.ProjectOut:
        return schemas.ProjectOut.of(services.create_project(body.key, body.name, uow_factory()))

    @app.get("/projects", response_model=list[schemas.ProjectOut])
    def list_projects() -> list[schemas.ProjectOut]:
        return [schemas.ProjectOut.of(project) for project in services.list_projects(uow_factory())]

    @app.get("/projects/{key}", response_model=schemas.ProjectOut, responses=errors)
    def get_project(key: str) -> schemas.ProjectOut:
        return schemas.ProjectOut.of(services.get_project(key, uow_factory()))

    @app.post(
        "/projects/{key}/issues",
        status_code=201,
        response_model=schemas.IssueOut,
        responses=errors,
    )
    def create_issue(key: str, body: schemas.IssueIn) -> schemas.IssueOut:
        return schemas.IssueOut.of(services.create_issue(key, body.title, uow_factory()))

    @app.get("/projects/{key}/issues", response_model=list[schemas.IssueOut], responses=errors)
    def list_issues(key: str, status: Status | None = None) -> list[schemas.IssueOut]:
        return [
            schemas.IssueOut.of(issue)
            for issue in services.list_issues(key, uow_factory(), status)
        ]

    @app.get("/issues/{ref}", response_model=schemas.IssueOut, responses=errors)
    def get_issue(ref: str) -> schemas.IssueOut:
        return schemas.IssueOut.of(services.get_issue(ref, uow_factory()))

    for path, name in TRANSITIONS.items():

        def transition(ref: str, name: str = name) -> schemas.IssueOut:
            return schemas.IssueOut.of(getattr(services, name)(ref, uow_factory()))

        transition.__name__ = name
        app.add_api_route(
            f"/issues/{{ref}}/{path}",
            transition,
            methods=["POST"],
            response_model=schemas.IssueOut,
            responses=errors,
        )

    @app.put("/issues/{ref}/assignee", response_model=schemas.IssueOut, responses=errors)
    def assign(ref: str, body: schemas.AssigneeIn) -> schemas.IssueOut:
        return schemas.IssueOut.of(services.assign(ref, body.user, uow_factory()))

    @app.delete("/issues/{ref}/assignee", response_model=schemas.IssueOut, responses=errors)
    def unassign(ref: str) -> schemas.IssueOut:
        return schemas.IssueOut.of(services.unassign(ref, uow_factory()))

    @app.post("/issues/{ref}/blockers", response_model=schemas.IssueOut, responses=errors)
    def add_blocker(ref: str, body: schemas.BlockerIn) -> schemas.IssueOut:
        return schemas.IssueOut.of(services.add_blocker(ref, body.blocker, uow_factory()))

    @app.delete("/issues/{ref}/blockers/{blocker}", response_model=schemas.IssueOut, responses=errors)
    def remove_blocker(ref: str, blocker: str) -> schemas.IssueOut:
        return schemas.IssueOut.of(services.remove_blocker(ref, blocker, uow_factory()))

    @app.post(
        "/issues/{ref}/comments",
        status_code=201,
        response_model=schemas.CommentOut,
        responses=errors,
    )
    def comment(ref: str, body: schemas.CommentIn) -> schemas.CommentOut:
        return schemas.CommentOut.of(services.comment(ref, body.body, uow_factory()))

    return app
