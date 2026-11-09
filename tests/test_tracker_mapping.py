"""The pinned tracker mapping and the recording adapters, without TLC."""

from __future__ import annotations

import hashlib
import pathlib
import unittest

from proof.tracker.mapping import ACTION_MAP, abstract, action_names, binding_for_method
from proof.tracker.recording import Patch, recording_stack
from proof.tracker.spec import invariant_names, max_issues, model_keys, model_users, next_actions
from proof.tracker.trace import config_text, constants, module_text, render_call
from tracker.bootstrap import in_memory, sqlite
from tracker.domain.errors import Rejected
from tracker.service_layer import services
from tracker.service_layer.errors import InvalidRef


ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = ROOT / "proof" / "tracker" / "MAPPING.sha256"


class MappingTests(unittest.TestCase):
    def test_mapping_file_matches_its_pin(self) -> None:
        digest = hashlib.sha256((ROOT / "proof" / "tracker" / "mapping.py").read_bytes()).hexdigest()
        self.assertEqual(PIN.read_text(encoding="utf-8").strip(), digest)

    def test_action_map_matches_next(self) -> None:
        self.assertEqual(action_names(), next_actions())
        self.assertEqual(len(next_actions()), 14)
        self.assertEqual(len(invariant_names()), 8)
        self.assertEqual(model_keys(), ("ENG",))
        self.assertEqual(model_users(), ("alice", "bob"))
        self.assertEqual(max_issues(), 3)

    def test_every_service_command_is_mapped_and_nothing_else(self) -> None:
        commands = {binding.method for binding in ACTION_MAP}
        for name in commands:
            self.assertTrue(callable(getattr(services, name)), name)
        for read in ("get_project", "list_projects", "get_issue", "list_issues", "parse_ref"):
            self.assertIsNone(binding_for_method(read))

    def test_abstract_view_collapses_titles_times_and_comments(self) -> None:
        uow = in_memory()
        services.create_project("OPS", "Operations", uow())
        services.create_project("ENG", "Engineering", uow())
        services.create_issue("ENG", "A long title", uow())
        services.create_issue("ENG", "Second", uow())
        services.assign("ENG-1", "alice", uow())
        services.triage("ENG-1", uow())
        services.start("ENG-1", uow())
        services.add_blocker("ENG-1", "ENG-2", uow())
        services.comment("ENG-1", "one", uow())
        services.comment("ENG-1", "two", uow())
        view = abstract(services.list_projects(uow()))
        self.assertEqual([project.key for project in view.projects], ["ENG", "OPS"])
        self.assertEqual(view.projects[1].issues, ())
        first = view.projects[0].issues[0]
        self.assertEqual(first.number, 1)
        self.assertEqual(first.title, "text")
        self.assertEqual(first.status, "inProgress")
        self.assertEqual(first.assignee, "alice")
        self.assertEqual(first.blocked_by, (2,))
        self.assertEqual(first.comments, 2)
        self.assertEqual(first.started_at, "set")
        self.assertEqual(first.completed_at, "unset")
        self.assertEqual(first.canceled_at, "unset")
        second = view.projects[0].issues[1]
        self.assertEqual(second.assignee, "unset")
        self.assertEqual(second.blocked_by, ())
        self.assertEqual(second.comments, 0)


class RecordingTests(unittest.TestCase):
    def _walk(self, factory):  # type: ignore[no-untyped-def]
        facade, recorder = recording_stack(factory)
        facade.create_project("ENG", "Engineering")
        facade.create_issue("ENG", "First")
        facade.triage("ENG-1")
        with self.assertRaises(Rejected):
            facade.start("ENG-1")
        facade.get_issue("ENG-1", facade.uow_factory())
        facade.assign("ENG-1", "bob", facade.uow_factory())
        with self.assertRaises(InvalidRef):
            facade.add_blocker("ENG-1", "OPS-1")
        return recorder.to_trace("walk", "test")

    def test_steps_are_the_same_over_the_fake_and_sqlite(self) -> None:
        fake = self._walk(in_memory())
        real = self._walk(sqlite())
        strip = lambda trace: [  # noqa: E731
            (step.action, step.key, step.number, step.arg, step.num, step.kind, step.post)
            for step in trace.steps
        ]
        self.assertEqual(strip(fake), strip(real))
        self.assertEqual(
            [(step.action, step.kind) for step in fake.steps],
            [
                ("CreateProject", "commit"),
                ("CreateIssue", "commit"),
                ("Triage", "commit"),
                ("Start", "refused"),
                ("Assign", "commit"),
            ],
        )
        self.assertEqual(fake.skipped, 1)
        self.assertEqual(fake.steps[3].post, fake.steps[2].post)
        self.assertEqual(fake.steps[4].arg, "bob")
        self.assertEqual(fake.steps[0].file, "tests/test_tracker_mapping.py")
        self.assertTrue(all(step.line > 0 for step in fake.steps))

    def test_a_foreign_unit_of_work_is_reported(self) -> None:
        facade, _recorder = recording_stack(in_memory())
        with self.assertRaises(RuntimeError) as caught:
            facade.create_project("ENG", "Engineering", in_memory()())
        self.assertEqual(str(caught.exception), "create_project finished without a unit of work")

    def test_patches_change_the_label_only(self) -> None:
        facade, recorder = recording_stack(
            in_memory(),
            Patch(rename=(("create_project", "Triage"),), force_kind=(("create_issue", "refused"),)),
        )
        facade.create_project("ENG", "Engineering")
        facade.create_issue("ENG", "First")
        self.assertEqual([step.action for step in recorder.steps], ["Triage", "CreateIssue"])
        self.assertEqual([step.kind for step in recorder.steps], ["commit", "refused"])
        self.assertEqual(len(recorder.steps[1].post.projects[0].issues), 1)

    def test_trace_module_and_config_follow_the_trace(self) -> None:
        trace = self._walk(in_memory())
        self.assertEqual(
            constants(trace),
            {"Keys": ("ENG",), "Users": ("bob",), "MaxIssues": 1, "MaxComments": 0},
        )
        config = config_text(trace)
        self.assertIn('Keys = {"ENG"}', config)
        self.assertIn('Users = {"bob"}', config)
        self.assertIn("INVARIANT NoBlockCycles", config)
        module = module_text(trace)
        self.assertIn('name = "AddBlocker" /\\ AddBlocker(key, n, num)', module)
        self.assertIn('name = "Assign" /\\ Assign(key, n, arg)', module)
        self.assertIn('kind |-> "refused"', module)
        self.assertEqual(render_call(trace.steps[4]), 'Assign("ENG", 1, "bob")')
        self.assertEqual(render_call(trace.steps[0]), 'CreateProject("ENG")')
