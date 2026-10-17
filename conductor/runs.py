"""The run tracker: what one run of the agent has done so far, from each folded harness line.

The conductor keeps what cooperation needs -- the run's LLM and tool calls, its last answer (keep-going reads it
for "NO MORE WORK",), and at the settle whether the session goes idle or straight on to a message the harness
held itself (pi's followUp). Pricing, tokens and plan figures belong to whoever runs it: a usage hook gets every
fold and adds its own fields to the run.
"""


def new_run(now, queued=0):
    """A run's start. [queued]: messages the harness itself holds, each the next run of its own."""
    run = {"start": now, "llm_calls": 0, "tool_calls": 0, "cost": 0.0}
    if queued:
        run["queued"] = queued
    return run


def apply(run, fold, usage=None):
    """One folded line into the run; True when anything changed. [usage] is called as usage(run, fold)."""
    before = dict(run)
    run["tool_calls"] += fold.tool_calls
    run["llm_calls"] += fold.llm_calls
    if fold.llm_calls_total is not None:
        run["llm_calls"] = fold.llm_calls_total
    for event in fold.events:
        if event.get("kind") == "text" and event.get("role") == "assistant":
            run["text"] = event.get("text") or ""
    if usage is not None:
        usage(run, fold)
    return run != before


def worth_a_summary(run):
    """A run that did nothing says nothing: "0 LLM calls · 0 tool calls · 0s" reads as a run that happened
    ."""
    return run.get("llm_calls", 0) > 0 or run.get("tool_calls", 0) > 0


def after_settle(run, now):
    """("idle", None), or ("next", the next run) when the harness goes straight on to a message it held."""
    if run.get("queued"):
        return "next", new_run(now, queued=run["queued"] - 1)
    return "idle", None
