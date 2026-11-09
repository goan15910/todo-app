"""Record tracker traces through every stack and check each one with TLC.

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

from proof.tracker.coverage import coverage_holds, report  # noqa: E402
from proof.tracker.explore import hypothesis_traces  # noqa: E402
from proof.tracker.negatives import negative_cases  # noqa: E402
from proof.tracker.scenarios import STACKS, recorded_scenarios  # noqa: E402
from proof.tracker.spec import SPEC  # noqa: E402
from proof.tracker.trace import validate_all, validate_one  # noqa: E402


def main() -> None:
    jar = os.environ.get("TLC_JAR")
    if not jar:
        sys.exit("TLC_JAR is not set")
    started = time.perf_counter()
    groups = {stack: recorded_scenarios(stack) for stack, _build in STACKS}
    explored = hypothesis_traces()
    if not explored:
        sys.exit("Hypothesis recorded no traces")
    groups["hypothesis"] = explored
    coverage_holds(groups)
    print(report(groups))
    traces = [trace for group in groups.values() for trace in group]
    if any(trace.skipped for trace in traces):
        sys.exit("a scenario made a call the mapping could not label")
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
            sys.exit(f"negative {case.trace.name} did not get stuck where expected")
        if verdict.file != step.file:
            sys.exit(1)
        source = (ROOT / step.file).read_text(encoding="utf-8").splitlines()
        if step.line < 1 or step.line > len(source) or case.method not in source[step.line - 1]:
            print(source[step.line - 1], file=sys.stderr)
            sys.exit(1)
    elapsed = time.perf_counter() - started
    print(f"elapsed_sec {elapsed:.3f}")
    print(f"model {SPEC}")


if __name__ == "__main__":
    main()
