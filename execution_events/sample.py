"""Small workload that exercises calls, returns, errors, and switches."""

from __future__ import annotations

import asyncio
import threading


def add(left: int, right: int) -> int:
    return left + right


def call_add() -> int:
    return add(2, 3)


def raise_value_error() -> None:
    raise ValueError("boom")


def catch_value_error() -> str:
    try:
        raise_value_error()
    except ValueError:
        return "caught"
    return "missed"


def reraise_value_error() -> None:
    try:
        raise_value_error()
    except ValueError:
        raise


def call_failing_builtin() -> str:
    try:
        int("nope")
    except ValueError:
        return "c-raised"
    return "missed"


def generate():
    yield 1
    return "done"


def drive_generator():
    iterator = generate()
    first = next(iterator)
    try:
        next(iterator)
    except StopIteration as stop:
        return first, stop.value


def throwing():
    try:
        yield "arm"
    except ValueError:
        yield "threw"


def throw_into_generator():
    iterator = throwing()
    next(iterator)
    try:
        return iterator.throw(ValueError("thrown"))
    finally:
        iterator.close()


async def ping() -> str:
    await asyncio.sleep(0)
    return "ping"


async def pong() -> str:
    await asyncio.sleep(0)
    return "pong"


async def switch_tasks() -> list[str]:
    first = asyncio.create_task(ping(), name="ping")
    second = asyncio.create_task(pong(), name="pong")
    return list(await asyncio.gather(first, second))


def worker() -> int:
    return add(10, 1)


def run_thread() -> None:
    thread = threading.Thread(target=worker, name="worker")
    thread.start()
    thread.join()


def call_builtin() -> int:
    return len("hi")


def run() -> None:
    call_add()
    catch_value_error()
    try:
        reraise_value_error()
    except ValueError:
        pass
    call_failing_builtin()
    drive_generator()
    throw_into_generator()
    asyncio.run(switch_tasks())
    run_thread()
    call_builtin()
