"""Keep-going wakes for long tasks.

The harness does it, not the model: pi has no scheduler, and Claude Code's own loop tools are not known
to fire in a headless session. Pure functions over the session record's `keep_going` field, so every
rule is a test; the host (worker.SupervisorHost) applies them.
"""
import re

INTERVAL_S, CAP = 600, 12
# A run this big that ended without the done line probably stopped mid-task: wake at once.
SIGNIFICANT_TOOLS, SIGNIFICANT_S = 5, 180
# Wakes in a row that made no tool call: the model has nothing to do, so stop paying for it.
NO_PROGRESS = 2
# A fired wake is not due again until its own run settles; JSON has no infinity.
NOT_UNTIL_SETTLED = 1e18

# At the start of a line only: the wake message quotes the phrase, and an echo of it must not disarm.
DONE = re.compile(r"^\s*NO MORE WORK\b:?\s*(.*)$", re.M)
ARM = re.compile(r"^\s*KEEP GOING\s*$", re.M)


def arm(interval_s=INTERVAL_S, cap=CAP, now=0):
    return {"interval_s": int(interval_s), "cap": int(cap), "count": 0, "idle_wakes": 0,
            "next_at": now + int(interval_s)}


def read(raw):
    """The stored setting, or None (off). Anything unreadable is off: a wake costs money."""
    if not isinstance(raw, dict):
        return None
    try:
        state = {"interval_s": int(raw["interval_s"]), "cap": int(raw["cap"]),
                 "count": int(raw.get("count", 0)), "idle_wakes": int(raw.get("idle_wakes", 0)),
                 "next_at": float(raw.get("next_at", 0))}
    except (KeyError, TypeError, ValueError):
        return None
    if state["interval_s"] < 0 or state["cap"] < 1:
        return None
    return state


def after_run(state, run, now):
    """A run settled. `run`: tool_calls, wall_s, wake (it was a wake), text (its last answer).
    Returns (new state or None, a line for the chat or None)."""
    text = run.get("text") or ""
    state = read(state)
    if state is None:
        if ARM.search(text):
            return arm(now=now), "keep going armed by the session"
        return None, None
    done = DONE.search(text)
    if done:
        why = done.group(1).strip() or "no reason given"
        return None, "keep going stopped: the session said no more work (%s)" % why
    if run.get("wake"):
        state["idle_wakes"] = 0 if run.get("tool_calls", 0) > 0 else state["idle_wakes"] + 1
        if state["idle_wakes"] >= NO_PROGRESS:
            return None, "keep going stopped: %d wakes in a row with no progress" % NO_PROGRESS
    big = run.get("tool_calls", 0) >= SIGNIFICANT_TOOLS or run.get("wall_s", 0) >= SIGNIFICANT_S
    state["next_at"] = now if big else now + state["interval_s"]
    return state, None


def due(state, now, idle, queue_empty):
    state = read(state)
    return state is not None and idle and queue_empty and now >= state["next_at"]


def fire(state, blocked_on="the person you work for"):
    """The next wake: (new state, the message, None), or (None, None, why) once the cap is reached."""
    state = read(state)
    if state is None:
        return None, None, None
    if state["count"] >= state["cap"]:
        return None, None, "keep going stopped: all %d wakes used" % state["cap"]
    state["count"] += 1
    text = ("[wake %d/%d] If the task is unfinished, continue from where you stopped. If it is finished, "
            "or you are blocked on %s, end your answer with a line `NO MORE WORK: <why>`."
            % (state["count"], state["cap"], blocked_on))
    # Not due again until this wake's own run settles and after_run sets it.
    state["next_at"] = NOT_UNTIL_SETTLED
    return state, text, None


def on_user_message(state):
    """The person wrote: the count starts again."""
    state = read(state)
    if state is None:
        return None
    state["count"] = 0
    state["idle_wakes"] = 0
    return state
