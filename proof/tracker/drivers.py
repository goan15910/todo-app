"""Ways to drive a scenario: the recording facade directly, or over HTTP.

A scenario is a function of one object with the service layer's command
names. ``RecordingServices`` is one such object. ``HttpDriver`` is another:
it turns each call into a request to the FastAPI app, whose routes call the
recording facade, so the same scenario yields a trace through the real
entrypoint. Refusals come back as ``Rejected`` so a scenario can treat both
drivers alike.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from proof.tracker.recording import Recorder, RecordingServices, recording_stack
from tracker.bootstrap import UnitOfWorkFactory
from tracker.domain.errors import Rejected
from tracker.entrypoints.fastapi_app import create_app


class HttpDriver:
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def create_project(self, key: str, name: str) -> Any:
        return self._call("post", "/projects", json={"key": key, "name": name})

    def create_issue(self, key: str, title: str) -> Any:
        return self._call("post", f"/projects/{key}/issues", json={"title": title})

    def triage(self, ref: str) -> Any:
        return self._call("post", f"/issues/{ref}/triage")

    def start(self, ref: str) -> Any:
        return self._call("post", f"/issues/{ref}/start")

    def submit_review(self, ref: str) -> Any:
        return self._call("post", f"/issues/{ref}/submit-review")

    def request_changes(self, ref: str) -> Any:
        return self._call("post", f"/issues/{ref}/request-changes")

    def approve(self, ref: str) -> Any:
        return self._call("post", f"/issues/{ref}/approve")

    def cancel(self, ref: str) -> Any:
        return self._call("post", f"/issues/{ref}/cancel")

    def reopen(self, ref: str) -> Any:
        return self._call("post", f"/issues/{ref}/reopen")

    def assign(self, ref: str, user: str) -> Any:
        return self._call("put", f"/issues/{ref}/assignee", json={"user": user})

    def unassign(self, ref: str) -> Any:
        return self._call("delete", f"/issues/{ref}/assignee")

    def add_blocker(self, ref: str, blocker: str) -> Any:
        return self._call("post", f"/issues/{ref}/blockers", json={"blocker": blocker})

    def remove_blocker(self, ref: str, blocker: str) -> Any:
        return self._call("delete", f"/issues/{ref}/blockers/{blocker}")

    def comment(self, ref: str, body: str) -> Any:
        return self._call("post", f"/issues/{ref}/comments", json={"body": body})

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = getattr(self._client, method)(path, **kwargs)
        if response.status_code in (404, 409, 422):
            raise Rejected(path, str(response.status_code), method)
        if response.status_code >= 300:
            raise RuntimeError(f"{method} {path} returned {response.status_code}: {response.text}")
        return response.json()


def http_stack(inner: UnitOfWorkFactory) -> tuple[HttpDriver, Recorder]:
    """A FastAPI app over ``inner`` with the recording facade wired in."""

    facade, recorder = recording_stack(inner)
    app = create_app(facade.uow_factory, services=facade)
    return HttpDriver(TestClient(app)), recorder


def direct_stack(inner: UnitOfWorkFactory) -> tuple[RecordingServices, Recorder]:
    return recording_stack(inner)
