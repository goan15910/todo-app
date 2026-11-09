"""Low gear: the few aggregate rules that are easier to see on the model itself."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tracker.domain.errors import IssueNotFound
from tracker.domain.model import Comment, Issue, IssueRef, Project, Status


STAMP = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


class ProjectModelTests(unittest.TestCase):
    def test_rebuilding_from_stored_issues_keeps_them(self) -> None:
        issues = (
            Issue("ENG", 1, "One", Status.DONE, "alice", frozenset({2}), (Comment("hi", STAMP),), STAMP, STAMP, None),
            Issue("ENG", 2, "Two", canceled_at=STAMP),
        )
        project = Project("ENG", "Engineering", issues, version=4)
        self.assertEqual(project.version, 4)
        self.assertEqual(project.issues, issues)
        self.assertEqual(project.next_number, 3)
        self.assertEqual(project.issue(1).blocked_by, frozenset({2}))
        self.assertEqual(project.issue(1).comments[0].body, "hi")
        self.assertEqual(project.issue(1).started_at, STAMP)
        self.assertEqual(project.issue(1).completed_at, STAMP)
        self.assertIsNone(project.issue(1).canceled_at)
        self.assertEqual(project.issue(1).assignee, "alice")
        self.assertEqual(project.issue(2).canceled_at, STAMP)
        self.assertIsNone(project.issue(2).assignee)
        self.assertEqual(repr(project), "Project('ENG', issues=2)")

    def test_issues_must_belong_and_be_dense(self) -> None:
        with self.assertRaises(ValueError) as foreign:
            Project("ENG", "x", (Issue("OPS", 1, "One"),))
        self.assertIn("does not belong to ENG", str(foreign.exception))
        with self.assertRaises(ValueError) as duplicate:
            Project("ENG", "x", (Issue("ENG", 1, "One"), Issue("ENG", 1, "Again")))
        self.assertIn("does not belong to ENG", str(duplicate.exception))
        with self.assertRaises(ValueError) as gap:
            Project("ENG", "x", (Issue("ENG", 2, "Two"),))
        self.assertEqual(str(gap.exception), "issue numbers of ENG are not dense")
        with self.assertRaises(ValueError) as key:
            Project("eng", "x")
        self.assertEqual(str(key.exception), "'eng' is not a project key like ENG")

    def test_issue_lookup_needs_an_int(self) -> None:
        project = Project("ENG", "x", (Issue("ENG", 1, "One"),))
        for number in (True, "1", 1.0):
            with self.subTest(number=number):
                with self.assertRaises(TypeError) as caught:
                    project.issue(number)  # type: ignore[arg-type]
                self.assertEqual(str(caught.exception), "issue number must be an int")
        with self.assertRaises(IssueNotFound):
            project.issue(0)
        with self.assertRaises(IssueNotFound):
            project.transitive_blockers(2)

    def test_identity(self) -> None:
        self.assertEqual(Project("ENG", "a"), Project("ENG", "b"))
        self.assertNotEqual(Project("ENG", "a"), Project("OPS", "a"))
        self.assertEqual(hash(Project("ENG", "a")), hash(Project("ENG", "b")))
        self.assertNotEqual(hash(Project("ENG", "a")), hash(Project("OPS", "a")))
        self.assertEqual(Issue("ENG", 1, "a"), Issue("ENG", 1, "b"))
        self.assertNotEqual(Issue("ENG", 1, "a"), Issue("ENG", 2, "a"))
        self.assertEqual(hash(Issue("ENG", 1, "a")), hash(IssueRef("ENG", 1)))
        self.assertEqual(Issue("ENG", 1, "a").__eq__(1), NotImplemented)
        self.assertEqual(Project("ENG", "a").__eq__(1), NotImplemented)
        self.assertEqual(str(IssueRef.parse("ENG-12")), "ENG-12")
        self.assertEqual(IssueRef.parse("ENG-12"), IssueRef("ENG", 12))
