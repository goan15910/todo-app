"""Record traces and check each one with TLC. One trace per file.

Requires TLC_JAR. The plain unit tests do not run this.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from proof.coverage import coverage_holds, report  # noqa: E402
from proof.explore import hypothesis_traces  # noqa: E402
from proof.negatives import negative_cases  # noqa: E402
from proof.scenarios import recorded_scenarios  # noqa: E402
from proof.spec import SPEC  # noqa: E402
from proof.todo_trace import validate_all, validate_one  # noqa: E402


def main() -> None:
    jar = os.environ.get("TLC_JAR")
    if not jar:
        sys.exit("TLC_JAR is not set")
    started = time.perf_counter()
    scenarios = recorded_scenarios()
    if scenarios[0].name != "scenario-1":
        sys.exit("scenario-1 was not the first recorded scenario")
    explored = hypothesis_traces()
    if not explored:
        sys.exit("Hypothesis recorded no traces")
    coverage_holds(scenarios, explored)
    text = report(scenarios, explored)
    print(text)
    traces = scenarios + explored
    print(f"validating {len(traces)} traces with TLC")
    verdicts = validate_all(traces, jar)
    failed = [verdict for verdict in verdicts if not verdict.ok]
    for verdict in verdicts:
        if verdict.ok:
            print(verdict.message)
    if failed:
        for verdict in failed:
            print(verdict.message, file=sys.stderr)
        sys.exit(1)
    for case in negative_cases():
        verdict = validate_one(case.trace, jar)
        step = case.trace.steps[case.stuck_index]
        print(verdict.message)
        if verdict.ok or verdict.kind != case.kind or verdict.line != step.line:
            sys.exit(1)
        if verdict.file != step.file:
            sys.exit(1)
        source = (ROOT / step.file).read_text(encoding="utf-8").splitlines()
        if step.line < 1 or step.line > len(source):
            sys.exit(1)
        if "service." not in source[step.line - 1]:
            print(source[step.line - 1], file=sys.stderr)
            sys.exit(1)
    elapsed = time.perf_counter() - started
    print(f"elapsed_sec {elapsed:.3f}")
    print(f"model {SPEC}")
    scenario_one = next(verdict for verdict in verdicts if verdict.name == "scenario-1")
    if not scenario_one.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
