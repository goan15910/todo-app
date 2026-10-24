"""Record Python execution events with ``sys.monitoring`` (PEP 669).

Requires Python 3.12 or newer. The recorder keeps calls, returns, raised
exceptions, exits, and coroutine switches, and stamps each event with the
thread that produced it. Passing ``files`` keeps the trace to those code
objects; omitting it records every other file in the process.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import CodeType
from typing import Iterable

_RECORDER = os.path.abspath(__file__)
_TEXT_LIMIT = 160
_TOOL_NAME = "execution_events"
_PREFERRED_TOOL_IDS = (3, 4, 0, 1, 2, 5)
_CLAIM_LOCK = threading.Lock()

# Lifecycle events. LINE, INSTRUCTION, BRANCH, and JUMP are intentionally
# absent: they are not calls, returns, raises, exits, or switches.
_KINDS = (
    "PY_START",
    "PY_RESUME",
    "PY_RETURN",
    "PY_YIELD",
    "CALL",
    "STOP_ITERATION",
    "RAISE",
    "RERAISE",
    "PY_UNWIND",
    "PY_THROW",
    "EXCEPTION_HANDLED",
    "C_RETURN",
    "C_RAISE",
)

_ROLES: dict[str, tuple[str, ...]] = {
    "PY_START": ("call",),
    "CALL": ("call",),
    "PY_RETURN": ("return", "exit"),
    "C_RETURN": ("return", "exit"),
    "RAISE": ("raise",),
    "RERAISE": ("raise",),
    "C_RAISE": ("raise",),
    "PY_THROW": ("raise", "switch"),
    "EXCEPTION_HANDLED": ("handled",),
    "PY_UNWIND": ("exit",),
    "STOP_ITERATION": ("exit",),
    "PY_YIELD": ("switch",),
    "PY_RESUME": ("switch",),
}

_CALL_KINDS = frozenset({"CALL", "C_RETURN", "C_RAISE"})
_VALUE_KINDS = frozenset({"PY_RETURN", "PY_YIELD"})
_EXCEPTION_KINDS = frozenset(
    {"RAISE", "RERAISE", "EXCEPTION_HANDLED", "PY_UNWIND", "PY_THROW", "STOP_ITERATION"}
)


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """One ``sys.monitoring`` callback, already turned into plain data."""

    kind: str
    roles: tuple[str, ...]
    thread_id: int
    thread_name: str
    filename: str
    qualname: str
    lineno: int
    offset: int
    scope: str
    task: str | None = None
    callable: str | None = None
    arg0: str | None = None
    value: str | None = None
    exception: str | None = None

    def __str__(self) -> str:
        parts = [self.kind, self.thread_name, self.qualname]
        if self.scope != "function":
            parts.append(self.scope)
        if self.kind in _CALL_KINDS:
            shown = "MISSING" if self.arg0 is None else self.arg0
            parts.append(f"callable={self.callable} arg0={shown}")
        if self.value is not None:
            parts.append(f"value={self.value}")
        if self.exception is not None:
            parts.append(f"exception={self.exception}")
        if self.task is not None:
            parts.append(f"task={self.task}")
        parts.append(f"{os.path.basename(self.filename)}:{self.lineno}")
        return " ".join(parts)


def _require_monitoring() -> None:
    if sys.version_info < (3, 12) or not hasattr(sys, "monitoring"):
        raise RuntimeError("execution_events requires Python 3.12+ (sys.monitoring)")


def _event_pairs() -> list[tuple[str, int]]:
    events = sys.monitoring.events
    return [(kind, getattr(events, kind)) for kind in _KINDS]


def _activatable_mask(pairs: list[tuple[str, int]]) -> int:
    # C_RETURN and C_RAISE are ancillary. set_events rejects those bits
    # unless they are paired in a way that still does not store them.
    # Enabling CALL and registering the callbacks is what delivers them.
    ancillary = sys.monitoring.events.C_RETURN | sys.monitoring.events.C_RAISE
    mask = 0
    for _kind, bit in pairs:
        if bit & ancillary:
            continue
        mask |= bit
    return mask


def _claim_tool_id() -> int:
    monitoring = sys.monitoring
    with _CLAIM_LOCK:
        for tool_id in _PREFERRED_TOOL_IDS:
            if monitoring.get_tool(tool_id) is None:
                monitoring.use_tool_id(tool_id, _TOOL_NAME)
                return tool_id
    raise RuntimeError("no free sys.monitoring tool id (0-5)")


def _silence(tool_id: int) -> None:
    monitoring = sys.monitoring
    try:
        monitoring.set_events(tool_id, monitoring.events.NO_EVENTS)
    except Exception:
        pass
    for _kind, bit in _event_pairs():
        try:
            monitoring.register_callback(tool_id, bit, None)
        except Exception:
            pass


def _free_tool_id(tool_id: int) -> None:
    try:
        sys.monitoring.free_tool_id(tool_id)
    except Exception:
        pass


def _norm(path: str) -> str:
    if path.startswith("<") and path.endswith(">"):
        return path
    return os.path.abspath(path)


def _short(obj: object) -> str:
    try:
        text = repr(obj)
    except Exception as exc:
        text = f"<unrepr {type(obj).__name__}: {type(exc).__name__}>"
    if len(text) > _TEXT_LIMIT:
        return text[: _TEXT_LIMIT - 3] + "..."
    return text


def _callable_name(fn: object) -> str:
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
    if not name:
        return type(fn).__name__
    module = getattr(fn, "__module__", None)
    if module and module != "builtins":
        return f"{module}.{name}"
    return str(name)


def _scope(code: CodeType) -> str:
    flags = code.co_flags
    if flags & inspect.CO_COROUTINE:
        return "coroutine"
    if flags & inspect.CO_ASYNC_GENERATOR:
        return "async_generator"
    if flags & inspect.CO_GENERATOR:
        return "generator"
    return "function"


def _lineno(code: CodeType, offset: int) -> int:
    line = code.co_firstlineno
    for start, _end, lineno in code.co_lines():
        if start is None or lineno is None:
            continue
        if start <= offset:
            line = lineno
        else:
            break
    return line


def _task_name() -> str | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    if task is None:
        return None
    return task.get_name()


class _Session:
    def __init__(self, files: Iterable[str] | None) -> None:
        if files is None:
            self._files: frozenset[str] | None = None
        else:
            self._files = frozenset(_norm(path) for path in files)
        self.events: list[ExecutionEvent] = []
        self._open = False
        self._tool_id: int | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        _require_monitoring()
        pairs = _event_pairs()
        tool_id = _claim_tool_id()
        self._tool_id = tool_id
        self._open = True
        monitoring = sys.monitoring
        try:
            for kind, bit in pairs:
                monitoring.register_callback(tool_id, bit, self._callback(kind))
            monitoring.set_events(tool_id, _activatable_mask(pairs))
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        with self._lock:
            tool_id = self._tool_id
            self._tool_id = None
            self._open = False
        if tool_id is None:
            return
        _silence(tool_id)
        _free_tool_id(tool_id)

    def _accepted(self, code: object) -> bool:
        filename = getattr(code, "co_filename", None)
        if not isinstance(filename, str):
            return False
        filename = _norm(filename)
        if filename == _RECORDER:
            return False
        if self._files is None:
            return True
        return filename in self._files

    def _callback(self, kind: str):
        def callback(*args: object) -> None:
            try:
                if not args or not self._accepted(args[0]):
                    return None
                event = self._event(kind, args)
            except Exception:
                return None
            with self._lock:
                if self._open and event is not None:
                    self.events.append(event)
            return None

        return callback

    def _event(self, kind: str, args: tuple[object, ...]) -> ExecutionEvent | None:
        code = args[0]
        if not isinstance(code, CodeType):
            return None
        offset = args[1] if len(args) > 1 and isinstance(args[1], int) else 0
        callable_name = None
        arg0 = None
        value = None
        exception = None
        if kind in _CALL_KINDS and len(args) >= 4:
            callable_name = _callable_name(args[2])
            if args[3] is not sys.monitoring.MISSING:
                arg0 = _short(args[3])
        elif kind in _VALUE_KINDS and len(args) >= 3:
            value = _short(args[2])
        elif kind in _EXCEPTION_KINDS and len(args) >= 3:
            exception = _short(args[2])
        return ExecutionEvent(
            kind=kind,
            roles=_ROLES.get(kind, ()),
            thread_id=threading.get_ident(),
            thread_name=threading.current_thread().name,
            filename=_norm(code.co_filename),
            qualname=code.co_qualname,
            lineno=_lineno(code, offset),
            offset=offset,
            scope=_scope(code),
            task=_task_name(),
            callable=callable_name,
            arg0=arg0,
            value=value,
            exception=exception,
        )


@contextmanager
def capture(files: Iterable[str] | None = None) -> Iterator[list[ExecutionEvent]]:
    """Record execution events until the ``with`` block exits.

    ``files`` is an optional set of source paths. When it is omitted, every
    code object outside this recorder is kept. The yielded list grows while
    the block runs and stops growing once the block leaves.
    """

    session = _Session(files)
    session.start()
    try:
        yield session.events
    finally:
        session.stop()


__all__ = ["ExecutionEvent", "capture"]
