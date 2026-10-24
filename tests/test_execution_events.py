"""Evidence that sys.monitoring capture sees real execution events."""

from __future__ import annotations

import os
import sys
import unittest

import execution_events.sample as sample
from execution_events import capture


def _matching(events, kind, qualname):
    return [event for event in events if event.kind == kind and event.qualname == qualname]


class CaptureTests(unittest.TestCase):
    def test_sample_records_calls_returns_errors_and_switches(self) -> None:
        with capture(files=[sample.__file__]) as events:
            sample.run()

        returned = _matching(events, "PY_RETURN", "add")
        self.assertIn("5", [event.value for event in returned])
        self.assertIn("11", [event.value for event in returned])
        self.assertIn("return", returned[0].roles)
        self.assertIn("exit", returned[0].roles)

        raised = _matching(events, "RAISE", "raise_value_error")
        self.assertTrue(raised)
        self.assertIn("ValueError", raised[0].exception or "")
        self.assertIn("raise", raised[0].roles)
        self.assertTrue(_matching(events, "PY_UNWIND", "raise_value_error"))
        self.assertTrue(_matching(events, "EXCEPTION_HANDLED", "catch_value_error"))
        self.assertTrue(_matching(events, "RERAISE", "reraise_value_error"))

        c_raised = [event for event in events if event.kind == "C_RAISE" and event.callable == "int"]
        self.assertTrue(c_raised)
        self.assertEqual(c_raised[0].arg0, "'nope'")

        yielded = _matching(events, "PY_YIELD", "generate")
        self.assertTrue(any(event.value == "1" for event in yielded))
        self.assertEqual(yielded[0].scope, "generator")
        self.assertTrue(_matching(events, "PY_RESUME", "generate"))
        self.assertTrue(
            any(event.value == "'done'" for event in _matching(events, "PY_RETURN", "generate"))
        )
        thrown = _matching(events, "PY_THROW", "throwing")
        self.assertTrue(any("ValueError" in (event.exception or "") for event in thrown))
        self.assertIn("switch", thrown[0].roles)

        flow = [
            (event.kind, event.qualname)
            for event in events
            if event.qualname in {"ping", "pong"}
            and event.kind in {"PY_START", "PY_YIELD", "PY_RESUME"}
        ]
        self.assertLess(flow.index(("PY_YIELD", "ping")), flow.index(("PY_START", "pong")))
        self.assertLess(flow.index(("PY_START", "pong")), flow.index(("PY_RESUME", "ping")))
        ping_yield = _matching(events, "PY_YIELD", "ping")
        self.assertEqual(ping_yield[0].task, "ping")
        self.assertEqual(ping_yield[0].scope, "coroutine")
        self.assertIn("switch", ping_yield[0].roles)
        self.assertTrue(any(event.task == "pong" for event in _matching(events, "PY_YIELD", "pong")))

        worker = [event for event in events if event.thread_name == "worker"]
        self.assertTrue(any(event.qualname == "worker" and event.kind == "PY_START" for event in worker))
        self.assertTrue(any(event.qualname == "add" and event.value == "11" for event in worker))
        main_ids = {event.thread_id for event in events if event.qualname == "call_add"}
        worker_ids = {event.thread_id for event in worker}
        self.assertTrue(main_ids)
        self.assertTrue(worker_ids.isdisjoint(main_ids))

        length_calls = [event for event in events if event.kind == "CALL" and event.callable == "len"]
        self.assertTrue(length_calls)
        self.assertEqual(length_calls[0].arg0, "'hi'")
        length_returns = [
            event for event in events if event.kind == "C_RETURN" and event.callable == "len"
        ]
        self.assertTrue(length_returns)
        self.assertIsNone(length_returns[0].value)
        self.assertTrue(
            any(event.value == "2" for event in _matching(events, "PY_RETURN", "call_builtin"))
        )

        finished = len(events)
        sample.call_add()
        self.assertEqual(len(events), finished)

    def test_file_filter_skips_other_modules(self) -> None:
        def not_sample() -> str:
            return "elsewhere"

        with capture(files=[sample.__file__]) as events:
            not_sample()
            sample.call_add()

        self.assertTrue(_matching(events, "PY_RETURN", "add"))
        self.assertFalse(any(event.qualname.endswith("not_sample") for event in events))

    def test_unfiltered_capture_sees_local_return(self) -> None:
        def local() -> int:
            return 7

        with capture() as events:
            local()

        matched = [
            event
            for event in events
            if event.kind == "PY_RETURN" and event.qualname.endswith("local") and event.value == "7"
        ]
        self.assertTrue(matched)
        recorder = os.path.join("execution_events", "__init__.py")
        self.assertFalse(
            any(event.filename.replace("\\", "/").endswith(recorder) for event in events)
        )

    def test_stop_releases_tool_and_survives_body_errors(self) -> None:
        before = [sys.monitoring.get_tool(tool_id) for tool_id in range(6)]
        with self.assertRaises(RuntimeError):
            with capture(files=[sample.__file__]) as events:
                sample.call_add()
                raise RuntimeError("stop")
        self.assertTrue(any(event.qualname == "add" for event in events))
        after = [sys.monitoring.get_tool(tool_id) for tool_id in range(6)]
        self.assertEqual(before, after)
        self.assertNotIn("execution_events", after)

    def test_nested_captures_both_record_the_call(self) -> None:
        with capture(files=[sample.__file__]) as outer:
            with capture(files=[sample.__file__]) as inner:
                sample.call_add()
        self.assertTrue(any(event.value == "5" for event in _matching(outer, "PY_RETURN", "add")))
        self.assertTrue(any(event.value == "5" for event in _matching(inner, "PY_RETURN", "add")))
        names = [sys.monitoring.get_tool(tool_id) for tool_id in range(6)]
        self.assertNotIn("execution_events", names)


if __name__ == "__main__":
    unittest.main()
