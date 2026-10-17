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
# Waiting on something that is not the person (CI, a build, a background job): the case keep-going exists
# for, so it never disarms. Models also write it as a NO MORE WORK whose reason is waiting; that counts as
# waiting unless the reason names the person (or "you"), who is then what it waits on.
WAITING = re.compile(r"^\s*WAITING\b:?\s*(.*)$", re.M)
_WAIT_WHY = re.compile(r"^\s*wait(ing)?\b", re.I)


def _waiting_for(text, done, person=None):
    """What the agent says it waits on, or None. Do not let a waiting NO MORE WORK disarm: "NO MORE WORK:
    waiting on CI for PR #458 …" did, and the agent was left to a wake that never came (akinoame1/app_infra#487)."""
    w = WAITING.search(text)
    if w:
        return w.group(1).strip() or "something"
    if done:
        why = done.group(1).strip()
        names = r"\b(you|your%s)\b" % ("|" + re.escape(person) if person else "")
        if _WAIT_WHY.match(why) and not re.search(names, why, re.I):
            return why
    return None


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


def after_run(state, run, now, person=None):
    """A run settled. `run`: tool_calls, wall_s, wake (it was a wake), text (its last answer).
    Returns (new state or None, a line for the chat or None)."""
    text = run.get("text") or ""
    state = read(state)
    if state is None:
        if ARM.search(text):
            return arm(now=now), "keep going armed by the session"
        return None, None
    done = DONE.search(text)
    waiting = _waiting_for(text, done, person)
    if done and waiting is None:
        why = done.group(1).strip() or "no reason given"
        return None, "keep going stopped: the session said no more work (%s)" % why
    if waiting is not None:
        # Waiting is not idling: a check on a build often needs no tool call. The cap still bounds the cost.
        state["idle_wakes"] = 0
        state["next_at"] = now + state["interval_s"]
        return state, "keep going waits: %s · next wake in %d min" % (waiting, max(1, round(state["interval_s"] / 60)))
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
    text = ("[wake %d/%d] If the task is unfinished, continue from where you stopped. If you are waiting on "
            "something that is not %s (CI, a build, a background job), end your answer with a line "
            "`WAITING: <what>` and you will be woken again to check. If it is finished, or you are blocked on "
            "%s, end your answer with a line `NO MORE WORK: <why>`."
            % (state["count"], state["cap"], blocked_on, blocked_on))
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
