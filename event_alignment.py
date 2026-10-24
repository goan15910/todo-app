"""Map captured execution events onto TLA+ variables and actions.

``execution_events`` records calls, returns, and errors. A return does not
carry the spec variables, so an ``Alignment`` names the returns that are
spec steps, the spec action for each step, and how to read the spec
variable from the implementation object. Values the implementation does
not store (``submitted``, on this spec) live in the history object the
alignment updates when a step returns.

A step is the successful return of a declared function. An unwind is not
a spec action. The trace is one spec variable, in the order those returns
were captured. Events keep their thread id; this does not invent a
thread-switch action.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Iterable

from execution_events import ExecutionEvent, capture

_TOOL_NAME = "event_alignment"
_TOOL_IDS = (3, 4, 0, 1, 2, 5)


class AlignmentError(Exception):
    """The captured events do not line up with the declared alignment."""


@dataclass(frozen=True, slots=True)
class SpecState:
    """One valuation of the aligned spec variable."""

    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        copied = dict(self.fields)
        for name, value in copied.items():
            if not isinstance(name, str):
                raise AlignmentError("spec field names must be strings")
            if not isinstance(value, (bool, str)):
                raise AlignmentError(f"spec field {name} must be a bool or a string")
        object.__setattr__(self, "fields", MappingProxyType(copied))

    def __getitem__(self, name: str) -> object:
        return self.fields[name]


@dataclass(frozen=True, slots=True)
class SpecStep:
    """One spec action between two valuations."""

    action: str
    pre: SpecState
    post: SpecState
    thread_id: int
    qualname: str


@dataclass(frozen=True, slots=True)
class MappedTrace:
    """Initial spec state plus the actions that follow it."""

    variable: str
    init: SpecState
    steps: tuple[SpecStep, ...]

    def states(self) -> tuple[SpecState, ...]:
        found = [self.init]
        found.extend(step.post for step in self.steps)
        return tuple(found)

    def actions(self) -> tuple[str, ...]:
        return tuple(step.action for step in self.steps)


@dataclass(frozen=True, slots=True)
class Alignment:
    """Declaration of how execution events become one spec variable.

    ``steps`` maps a function ``qualname`` to a chart event. The successful
    return of that function is one spec step. ``actions`` maps
    ``(state, event)`` to a spec action name; ``state`` is the projected
    ``state_field`` before the step. ``project`` reads the spec fields from
    the bound object and the history. ``advance`` updates that history
    after the action is known. ``project`` and ``advance`` must not call
    back into the implementation.
    """

    variable: str
    steps: Mapping[str, str]
    actions: Mapping[tuple[str, str], str]
    project: Callable[[object, object], Mapping[str, object]]
    advance: Callable[[object, str], object]
    initial_history: object
    state_field: str = "state"


@dataclass(frozen=True, slots=True)
class _Snap:
    qualname: str
    filename: str
    thread_id: int
    action: str
    pre: SpecState
    post: SpecState


def _require_monitoring() -> None:
    if sys.version_info < (3, 12) or not hasattr(sys, "monitoring"):
        raise RuntimeError("event_alignment requires Python 3.12+ (sys.monitoring)")


def _claim_tool_id() -> int:
    monitoring = sys.monitoring
    for tool_id in _TOOL_IDS:
        if monitoring.get_tool(tool_id) is None:
            monitoring.use_tool_id(tool_id, _TOOL_NAME)
            return tool_id
    raise RuntimeError("no free sys.monitoring tool id (0-5)")


def _norm(path: str) -> str:
    if path.startswith("<") and path.endswith(">"):
        return path
    return os.path.abspath(path)


class _Watch:
    def __init__(self, alignment: Alignment) -> None:
        self.alignment = alignment
        self.obj: object | None = None
        self.history = alignment.initial_history
        self.error: str | None = None
        self.taken: list[_Snap] = []
        self._stack: dict[int, list[tuple[str, str, SpecState]]] = {}
        self._lock = threading.RLock()
        self._tool_id: int | None = None

    def bind(self, obj: object) -> None:
        with self._lock:
            if self.obj is not None:
                raise AlignmentError("one implementation object can be bound")
            self.obj = obj

    def start(self) -> None:
        _require_monitoring()
        tool_id = _claim_tool_id()
        self._tool_id = tool_id
        events = sys.monitoring.events
        try:
            sys.monitoring.register_callback(tool_id, events.CALL, self._safe(self._on_call))
            sys.monitoring.register_callback(tool_id, events.PY_START, self._safe(self._on_start))
            sys.monitoring.register_callback(tool_id, events.PY_RETURN, self._safe(self._on_return))
            sys.monitoring.register_callback(tool_id, events.PY_UNWIND, self._safe(self._on_unwind))
            sys.monitoring.set_events(
                tool_id,
                events.CALL | events.PY_START | events.PY_RETURN | events.PY_UNWIND,
            )
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        tool_id = self._tool_id
        self._tool_id = None
        if tool_id is None:
            return
        monitoring = sys.monitoring
        try:
            monitoring.set_events(tool_id, monitoring.events.NO_EVENTS)
        except Exception:
            pass
        for bit in (
            monitoring.events.CALL,
            monitoring.events.PY_START,
            monitoring.events.PY_RETURN,
            monitoring.events.PY_UNWIND,
        ):
            try:
                monitoring.register_callback(tool_id, bit, None)
            except Exception:
                pass
        try:
            monitoring.free_tool_id(tool_id)
        except Exception:
            pass

    def mapped(self, events: list[ExecutionEvent]) -> MappedTrace:
        if self.error:
            raise AlignmentError(self.error)
        returns = [
            event
            for event in events
            if event.kind == "PY_RETURN" and event.qualname in self.alignment.steps
        ]
        if len(returns) != len(self.taken):
            raise AlignmentError(
                f"{len(returns)} step returns in the capture and {len(self.taken)} snapshots"
            )
        steps: list[SpecStep] = []
        previous = None
        for event, snap in zip(returns, self.taken):
            if event.qualname != snap.qualname or event.thread_id != snap.thread_id:
                raise AlignmentError("captured step does not match the snapshot")
            if _norm(event.filename) != snap.filename:
                raise AlignmentError("captured step does not match the snapshot")
            event_name = self.alignment.steps[event.qualname]
            try:
                action = self.alignment.actions[(snap.pre[self.alignment.state_field], event_name)]
            except KeyError as exc:
                raise AlignmentError(
                    f"no spec action for {snap.pre[self.alignment.state_field]} {event_name}"
                ) from exc
            if action != snap.action:
                raise AlignmentError("captured step does not match the snapshot")
            if previous is None:
                previous = snap.pre
            elif snap.pre != previous:
                raise AlignmentError("spec fields changed between spec steps")
            if snap.post == snap.pre:
                raise AlignmentError(f"{snap.action} did not change the spec state")
            steps.append(
                SpecStep(
                    action=snap.action,
                    pre=snap.pre,
                    post=snap.post,
                    thread_id=snap.thread_id,
                    qualname=snap.qualname,
                )
            )
            previous = snap.post
        if steps:
            init = steps[0].pre
        elif self.obj is None:
            raise AlignmentError("bind the implementation object before mapping")
        else:
            init = SpecState(self.alignment.project(self.obj, self.history))
        return MappedTrace(self.alignment.variable, init, tuple(steps))

    def _fail(self, message: str) -> None:
        if self.error is None:
            self.error = message

    def _safe(self, callback: Callable[..., None]) -> Callable[..., None]:
        def wrapped(*args: object) -> None:
            try:
                callback(*args)
            except Exception as exc:
                with self._lock:
                    self._fail(f"{type(exc).__name__}: {exc}")
            return None

        return wrapped

    def _on_call(self, _code: object, _offset: object, fn: object, arg0: object) -> None:
        qualname = getattr(fn, "__qualname__", None)
        if qualname not in self.alignment.steps:
            return
        with self._lock:
            if self.obj is None:
                self._fail("bind the implementation object before a spec step")
            elif arg0 is not self.obj:
                self._fail("spec step was called on an object that was not bound")

    def _on_start(self, code: object, _offset: object) -> None:
        qualname = getattr(code, "co_qualname", None)
        if qualname not in self.alignment.steps:
            return
        thread_id = threading.get_ident()
        with self._lock:
            if self.error or self.obj is None:
                self._fail("bind the implementation object before a spec step")
                return
            filename = _norm(getattr(code, "co_filename", ""))
            pre = SpecState(self.alignment.project(self.obj, self.history))
            self._stack.setdefault(thread_id, []).append((qualname, filename, pre))

    def _on_return(self, code: object, _offset: object, _value: object) -> None:
        qualname = getattr(code, "co_qualname", None)
        if qualname not in self.alignment.steps:
            return
        thread_id = threading.get_ident()
        with self._lock:
            if self.error:
                return
            frame = self._pop(thread_id, qualname)
            if frame is None:
                return
            _name, filename, pre = frame
            event_name = self.alignment.steps[qualname]
            state_name = pre[self.alignment.state_field]
            action = self.alignment.actions.get((state_name, event_name))
            if action is None:
                self._fail(f"no spec action for {state_name} {event_name}")
                return
            self.history = self.alignment.advance(self.history, action)
            post = SpecState(self.alignment.project(self.obj, self.history))
            self.taken.append(
                _Snap(qualname, filename, thread_id, action, pre, post)
            )

    def _on_unwind(self, code: object, _offset: object, _exc: object) -> None:
        qualname = getattr(code, "co_qualname", None)
        if qualname not in self.alignment.steps:
            return
        thread_id = threading.get_ident()
        with self._lock:
            if self.error or self.obj is None:
                return
            frame = self._pop(thread_id, qualname)
            if frame is None:
                return
            _name, _filename, pre = frame
            post = SpecState(self.alignment.project(self.obj, self.history))
            if post != pre:
                self._fail(f"{qualname} changed spec fields while unwinding")

    def _pop(self, thread_id: int, qualname: str) -> tuple[str, str, SpecState] | None:
        stack = self._stack.get(thread_id)
        if not stack:
            self._fail(f"{qualname} returned without a start event")
            return None
        frame = stack.pop()
        if frame[0] != qualname:
            self._fail(f"{qualname} returned while {frame[0]} was still open")
            return None
        return frame


class TraceRun:
    """A capture plus the alignment snapshots taken during it."""

    def __init__(self, watch: _Watch) -> None:
        self.events: list[ExecutionEvent] = []
        self._watch = watch

    def bind(self, obj: object) -> None:
        """Read spec fields from ``obj`` at each declared step."""

        self._watch.bind(obj)

    def mapped(self) -> MappedTrace:
        """Spec states and actions for the steps in ``events``."""

        return self._watch.mapped(self.events)


@contextmanager
def trace(
    alignment: Alignment, files: Iterable[str] | None = None
) -> Iterator[TraceRun]:
    """Capture execution events and align the declared steps.

    ``files`` is passed to ``execution_events.capture``. It must include
    the modules that contain the step functions, or those returns will not
    be in the capture.
    """

    watch = _Watch(alignment)
    run = TraceRun(watch)
    watch.start()
    try:
        with capture(files=files) as events:
            run.events = events
            yield run
    finally:
        watch.stop()


def _tla_value(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise AlignmentError(f"cannot render {type(value).__name__} as a TLA+ value")


def _tla_record(state: SpecState) -> str:
    parts = [f"{name} |-> {_tla_value(value)}" for name, value in state.fields.items()]
    return "[" + ", ".join(parts) + "]"


def trace_module(mapped: MappedTrace, *, spec: str = "TodoTask") -> str:
    """A module whose only behavior is this trace, checked against ``spec``'s ``Next``."""

    records = ",\n  ".join(_tla_record(state) for state in mapped.states())
    variable = mapped.variable
    claimed = ", ".join(mapped.actions()) or "(none)"
    return f"""---- MODULE MappedTrace ----
EXTENDS {spec}, Naturals, Sequences, TLC

\\* Claimed actions, in order: {claimed}

Trace == <<
  {records}
>>

TraceInit == {variable} = Trace[1]

\\* One record can be visited twice. Without this view TLC would not
\\* take the second visit's successors.
Position == <<TLCGet("level"), {variable}>>

Matches ==
  \\E i \\in 1..(Len(Trace) - 1) :
    /\\ TLCGet("level") = i
    /\\ {variable} = Trace[i]
    /\\ {variable}' = Trace[i + 1]
====
"""


def trace_config(invariants: Iterable[str] = ()) -> str:
    """Config for ``trace_module``. Deadlock is off: the trace is finite.

    ``NEXT`` is the spec's ``Next``. ``Matches`` keeps only the recorded
    states, so TLC reports the spec action that produced each one.
    A complete trace has search depth equal to the number of states.
    """

    lines = [
        "INIT TraceInit",
        "NEXT Next",
        "VIEW Position",
        "ACTION_CONSTRAINT Matches",
        "CHECK_DEADLOCK FALSE",
    ]
    for name in invariants:
        lines.append(f"INVARIANT {name}")
    return "\n".join(lines) + "\n"


__all__ = [
    "Alignment",
    "AlignmentError",
    "MappedTrace",
    "SpecState",
    "SpecStep",
    "TraceRun",
    "trace",
    "trace_config",
    "trace_module",
]
