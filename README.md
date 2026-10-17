# agent-conductor

Managing an AI coding agent's work so it cooperates. A small Python library (standard library only) that sits
between a person and an agent harness — pi or Claude Code today — and
handles what goes wrong when you actually work with one:

- **The queue.** What you write while the agent works is held, visible and editable, and delivered one message
  at a time after each run. Harnesses that hold follow-ups themselves do it invisibly, and weaker models forget
  them or answer only one. You can reorder, edit, discard, send one early (it is delivered later with a reminder
  so it is not redone), or mark *stop after this* to read the answer before the rest goes in.
- **Delivery instructions.** What is added to a message on its way: your own wrapper (reply rules, say), the
  quote of the message you replied to, a note of attached files and where they are, the reminder.
- **Keep-going.** Wakes for a long task the agent stopped mid-way: on an interval or right after a big run, and
  disarmed by a `NO MORE WORK: <why>` line, two wakes in a row with no progress, a cap, or the host.
- **Harness adapters.** What each line of a harness's output means: answers, tool calls, when a run has really
  ended, refusals. pi (RPC mode) and Claude Code (stream-json) are included.

Pricing and usage are not its business: a host passes hooks and keeps its own accounting.

## How a host uses it

`conductor.engine.Conductor` does the wiring over ports the host supplies:

- a **store** — the session's record and event log, and a queue store (`conductor.queue.FileQueueStore` or your
  own `load`/`save`);
- a **transport** — `send(sid, payload)` to the running harness;
- its **policy** — the wrapper text, an optional gate (a spend cap), usage hooks, the summary line of a run.

```python
from conductor.engine import Conductor

c = Conductor(store, transport, wrapper="First line: what you will do with this.")
c.submit(sid, "fix the flaky test", "fix the flaky test", harness)   # straight in, or held while it works
for line in new_output_lines:
    settled, refused = c.fold_line(sid, store.get(sid), harness, line, memo, now)
    if settled and not refused:
        c.drain(sid)                                                    # the next held message, unless paused
c.keep_going_tick(records, now, running=lambda sid: True, harness_for=lambda rec: harness)
```

The ports are listed at the top of `conductor/engine.py`; `tests/test_engine.py` runs the engine over in-memory
ones and is the shortest working example. The phone app it grew up in is one client; a terminal client is next.

## Status

0.1: the library, extracted from a working server with its tests. A reference host (files and a subprocess
transport) and a terminal client are planned.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md): forks are welcome; outside contributions are not taken here.

MIT licensed.
