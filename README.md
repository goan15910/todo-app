# todo-app

## Specification

`spec/TodoTask.tla` is one task from Clew's b-full chart (`examples/todo/b-full.yaml`). TLC checks `TypeOK` and the chart guards as invariants: `TitleNotEmpty` and `CompletedAtSet`.

GitHub Actions runs TLC on pull requests and on `main`. Locally, with Java 21:

```bash
curl -fsSL -o /tmp/tla2tools.jar https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar
java -XX:+UseParallelGC -jar /tmp/tla2tools.jar spec/TodoTask.tla
```

## Execution events

`python -m execution_events` prints JSON Lines for one walk of the b-full task flow (`todo_app.task`, the same edges as `spec/TodoTask.tla`). Each line has `kind`, `qualname`, `thread_id`, `thread_name`, `file`, and `line`. `value`, `exception`, `arg0`, `task`, and `callable` are included when that event has them.

```bash
python -m execution_events
```

## Alignment

Those events are calls and returns, not values of `task`. `event_alignment` is the declaration of which returns are spec steps. `todo_app.alignment` maps this task onto `spec/TodoTask.tla`: the action for each step, and the `task` record read from the live object. `submitted` is remembered there; the events do not carry it.

```python
import todo_app.task as tasks
from event_alignment import trace
from todo_app.alignment import todo_alignment

with trace(todo_alignment, files=[tasks.__file__]) as run:
    item = tasks.TodoTask("")
    run.bind(item)
    item.set_title("buy milk")
    item.submit()

mapped = run.mapped()
```

A JSON Lines log from the recorder uses the same declaration. Each line has `kind`, `qualname`, `thread_id`, `thread_name`, `file`, and `line`, plus `value`, `exception`, `arg0`, and `task` when the event has them. The log does not carry `task`. Pass the spec record from before the first step; `effect` names each successor.

```python
from event_alignment import map_log, read_jsonl

mapped = map_log(read_jsonl(open("trace.jsonl")), todo_alignment, initial)
```
