"""The FastAPI entrypoint over SQLite: one happy path per route, the error codes, and 422s."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from tracker.bootstrap import sqlite
from tracker.entrypoints.fastapi_app import TRANSITIONS, create_app


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(sqlite()))

    def _project(self, key: str = "ENG") -> None:
        response = self.client.post("/projects", json={"key": key, "name": "Engineering"})
        self.assertEqual(response.status_code, 201, response.text)

    def _issue(self, key: str = "ENG", title: str = "First") -> str:
        response = self.client.post(f"/projects/{key}/issues", json={"title": title})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["ref"]

    def test_projects(self) -> None:
        self._project()
        self.assertEqual(
            self.client.get("/projects/ENG").json(),
            {"key": "ENG", "name": "Engineering", "version": 1, "issue_count": 0},
        )
        self.assertEqual([item["key"] for item in self.client.get("/projects").json()], ["ENG"])
        duplicate = self.client.post("/projects", json={"key": "ENG", "name": "Again"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json(), {"detail": "project ENG already exists"})
        self.assertEqual(self.client.get("/projects/OPS").status_code, 404)
        self.assertEqual(self.client.post("/projects", json={"key": "eng", "name": "x"}).status_code, 422)
        self.assertEqual(self.client.post("/projects", json={"name": "x"}).status_code, 422)

    def test_issue_lifecycle(self) -> None:
        self._project()
        ref = self._issue()
        self.assertEqual(ref, "ENG-1")
        issue = self.client.get("/issues/ENG-1").json()
        self.assertEqual(issue["status"], "backlog")
        self.assertIsNone(issue["assignee"])
        self.assertEqual(issue["blocked_by"], [])
        self.assertEqual(issue["comments"], [])
        self.assertIsNone(issue["started_at"])
        self.assertEqual(self.client.post("/issues/ENG-1/triage").json()["status"], "todo")
        refused = self.client.post("/issues/ENG-1/start")
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json(), {"detail": "start rejected for ENG-1 in todo"})
        assigned = self.client.put("/issues/ENG-1/assignee", json={"user": "alice"})
        self.assertEqual(assigned.json()["assignee"], "alice")
        started = self.client.post("/issues/ENG-1/start").json()
        self.assertEqual(started["status"], "inProgress")
        self.assertIsNotNone(started["started_at"])
        self.assertEqual(self.client.post("/issues/ENG-1/submit-review").json()["status"], "inReview")
        self.assertEqual(self.client.post("/issues/ENG-1/request-changes").json()["status"], "inProgress")
        self.assertEqual(self.client.post("/issues/ENG-1/submit-review").json()["status"], "inReview")
        done = self.client.post("/issues/ENG-1/approve").json()
        self.assertEqual(done["status"], "done")
        self.assertIsNotNone(done["completed_at"])
        self.assertEqual(self.client.delete("/issues/ENG-1/assignee").json()["assignee"], None)
        reopened = self.client.post("/issues/ENG-1/reopen").json()
        self.assertEqual(reopened["status"], "todo")
        self.assertIsNone(reopened["completed_at"])
        canceled = self.client.post("/issues/ENG-1/cancel").json()
        self.assertEqual(canceled["status"], "canceled")
        self.assertIsNotNone(canceled["canceled_at"])
        self.assertEqual(self.client.get("/projects/ENG").json()["issue_count"], 1)

    def test_every_transition_route_exists(self) -> None:
        self._project()
        self._issue()
        for path in TRANSITIONS:
            with self.subTest(path=path):
                response = self.client.post(f"/issues/ENG-1/{path}")
                self.assertIn(response.status_code, {200, 409}, response.text)
                self.assertEqual(self.client.post(f"/issues/ENG-7/{path}").status_code, 404)
                self.assertEqual(self.client.post(f"/issues/nope/{path}").status_code, 422)

    def test_blockers_and_comments(self) -> None:
        self._project()
        self._issue()
        self._issue(title="Second")
        blocked = self.client.post("/issues/ENG-1/blockers", json={"blocker": "ENG-2"})
        self.assertEqual(blocked.status_code, 200, blocked.text)
        self.assertEqual(blocked.json()["blocked_by"], ["ENG-2"])
        cycle = self.client.post("/issues/ENG-2/blockers", json={"blocker": "ENG-1"})
        self.assertEqual(cycle.status_code, 409)
        self.assertEqual(self.client.post("/issues/ENG-1/blockers", json={"blocker": "ENG-9"}).status_code, 404)
        self.assertEqual(self.client.post("/issues/ENG-1/blockers", json={"blocker": "OPS-1"}).status_code, 422)
        comment = self.client.post("/issues/ENG-1/comments", json={"body": "note"})
        self.assertEqual(comment.status_code, 201)
        self.assertEqual(comment.json()["body"], "note")
        self.assertEqual([item["body"] for item in self.client.get("/issues/ENG-1").json()["comments"]], ["note"])
        removed = self.client.delete("/issues/ENG-1/blockers/ENG-2")
        self.assertEqual(removed.json()["blocked_by"], [])
        self.assertEqual(self.client.delete("/issues/ENG-1/blockers/ENG-2").status_code, 409)

    def test_listing_and_filtering(self) -> None:
        self._project()
        self._issue()
        self._issue(title="Second")
        self.client.post("/issues/ENG-1/triage")
        self.assertEqual(
            [item["ref"] for item in self.client.get("/projects/ENG/issues").json()],
            ["ENG-1", "ENG-2"],
        )
        self.assertEqual(
            [item["ref"] for item in self.client.get("/projects/ENG/issues", params={"status": "todo"}).json()],
            ["ENG-1"],
        )
        self.assertEqual(self.client.get("/projects/ENG/issues", params={"status": "nope"}).status_code, 422)
        self.assertEqual(self.client.get("/projects/OPS/issues").status_code, 404)
        self.assertEqual(self.client.get("/issues/ENG-3").status_code, 404)
        self.assertEqual(self.client.get("/issues/ENG-x").status_code, 422)

    def test_malformed_bodies_never_reach_the_service_layer(self) -> None:
        self._project()
        self._issue()
        self.assertEqual(self.client.post("/projects/ENG/issues", json={}).status_code, 422)
        empty = self.client.post("/projects/ENG/issues", json={"title": ""})
        self.assertEqual(empty.status_code, 409)
        self.assertEqual(empty.json(), {"detail": "issue title in ENG must not be empty"})
        self.assertEqual(self.client.put("/issues/ENG-1/assignee", json={"user": 1}).status_code, 422)
        self.assertEqual(self.client.put("/issues/ENG-1/assignee", json={"user": ""}).status_code, 409)
        self.assertEqual(self.client.post("/issues/ENG-1/comments", json={"text": "x"}).status_code, 422)
        self.assertEqual(self.client.get("/projects/ENG").json()["issue_count"], 1)
