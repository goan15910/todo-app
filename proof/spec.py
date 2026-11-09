"""Read the frozen TodoTask model. The mapping is checked against this."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "TodoTask.tla"
CFG = ROOT / "spec" / "TodoTask.cfg"

_NEXT = re.compile(r"(?m)^Next ==\n((?:  \\/ \w+\n)+)")
_MAX = re.compile(r"(?m)^MaxTasks\s*=\s*(\d+)\s*$")
_INVARIANT = re.compile(r"(?m)^INVARIANT\s+(\w+)\s*$")


def next_actions() -> tuple[str, ...]:
    match = _NEXT.search(SPEC.read_text(encoding="utf-8"))
    if match is None:
        raise RuntimeError("spec/TodoTask.tla has no Next disjunction")
    names = tuple(re.findall(r"\\/ (\w+)", match.group(1)))
    if not names or len(names) != len(set(names)):
        raise RuntimeError("spec/TodoTask.tla has duplicate Next actions")
    return names


def max_tasks() -> int:
    match = _MAX.search(CFG.read_text(encoding="utf-8"))
    if match is None:
        raise RuntimeError("spec/TodoTask.cfg does not set MaxTasks")
    return int(match.group(1))


def invariant_names() -> tuple[str, ...]:
    names = tuple(_INVARIANT.findall(CFG.read_text(encoding="utf-8")))
    if not names:
        raise RuntimeError("spec/TodoTask.cfg has no invariants")
    return names
