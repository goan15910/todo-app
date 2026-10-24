"""Print a JSON Lines log of one b-full todo-task walk."""

from __future__ import annotations

import json

import todo_app.task as tasks
from execution_events import capture
from execution_events.todo_flow import run_todo_flow


def main() -> None:
    with capture(files=[tasks.__file__]) as events:
        run_todo_flow()
    for event in events:
        print(json.dumps(event.as_record(), ensure_ascii=False))


if __name__ == "__main__":
    main()
