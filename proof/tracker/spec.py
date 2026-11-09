"""Read the frozen IssueTracker model. The mapping is checked against this."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec" / "IssueTracker.tla"
CFG = ROOT / "spec" / "IssueTracker.cfg"

_NEXT_BLOCK = re.compile(r"(?ms)^Next ==\n(.*?)^Spec ==")
_CALL = re.compile(r"(?:\\/ |: )([A-Z]\w*)\(")
_INVARIANT = re.compile(r"(?m)^INVARIANT\s+(\w+)\s*$")
_MAX_ISSUES = re.compile(r"(?m)^MaxIssues\s*=\s*(\d+)\s*$")
_KEYS = re.compile(r'(?m)^Keys\s*=\s*\{([^}]*)\}')
_USERS = re.compile(r'(?m)^Users\s*=\s*\{([^}]*)\}')


def next_actions() -> tuple[str, ...]:
    match = _NEXT_BLOCK.search(SPEC.read_text(encoding="utf-8"))
    if match is None:
        raise RuntimeError("spec/IssueTracker.tla has no Next definition")
    names = tuple(_CALL.findall(match.group(1)))
    if not names or len(names) != len(set(names)):
        raise RuntimeError("spec/IssueTracker.tla has duplicate Next actions")
    return names


def invariant_names() -> tuple[str, ...]:
    names = tuple(_INVARIANT.findall(CFG.read_text(encoding="utf-8")))
    if not names:
        raise RuntimeError("spec/IssueTracker.cfg has no invariants")
    return names


def max_issues() -> int:
    match = _MAX_ISSUES.search(CFG.read_text(encoding="utf-8"))
    if match is None:
        raise RuntimeError("spec/IssueTracker.cfg does not set MaxIssues")
    return int(match.group(1))


def _strings(pattern: re.Pattern[str]) -> tuple[str, ...]:
    match = pattern.search(CFG.read_text(encoding="utf-8"))
    if match is None:
        raise RuntimeError(f"spec/IssueTracker.cfg does not set {pattern.pattern[6:11]}")
    return tuple(re.findall(r'"([^"]*)"', match.group(1)))


def model_keys() -> tuple[str, ...]:
    return _strings(_KEYS)


def model_users() -> tuple[str, ...]:
    return _strings(_USERS)
