"""Sequential ids for new tasks."""

from __future__ import annotations


class SequentialIdAllocator:
    """Ids 1, 2, 3, ... in the order ``allocate`` is called."""

    def __init__(self) -> None:
        self._next = 1

    def peek(self) -> int:
        return self._next

    def allocate(self) -> int:
        allocated = self._next
        self._next += 1
        return allocated
