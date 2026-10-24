# todo-app

## Specification

`spec/TodoTask.tla` is one task from Clew's b-full chart (`examples/todo/b-full.yaml`). TLC checks `TypeOK` and the chart guards as invariants: `TitleNotEmpty` and `CompletedAtSet`.

GitHub Actions runs TLC on pull requests and on `main`. Locally, with Java 21:

```bash
curl -fsSL -o /tmp/tla2tools.jar https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar
java -XX:+UseParallelGC -jar /tmp/tla2tools.jar spec/TodoTask.tla
```
