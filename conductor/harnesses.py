"""The dev sessions' one switch between harnesses: pi, Claude Code, and whatever comes next.

Everything a harness does differently lives behind
[Harness]: the command line that starts it, the lines a prompt or a model change becomes, and what
each line it prints means. The worker, the supervisor, the API and the phone know none of it — they
see the same events (`text`, `tool_call`, `tool_result`, `notice`, `cost`) whichever harness ran.

A new harness is one module with a `Harness` subclass and one line in [registry]; its executable
goes on the supervisor's allow-list (`harnesses.allow`), which is read at every start, so adding one
never restarts the supervisor.
"""
from dataclasses import dataclass, field


@dataclass
class Fold:
    """What one raw harness line means, in the worker's terms."""
    events: list = field(default_factory=list)   # what the phone draws, in order
    call_started: bool = False      # an LLM call took up the oldest delivered message (→ read)
    llm_calls: int = 0              # LLM calls this line adds to the run
    llm_calls_total: int = None     # or the run's total, when the harness counts for us
    tool_calls: int = 0
    cost: float = 0.0               # dollars this line adds to the run
    settled: bool = False           # the run is over; the next message may go
    refused: str = None             # the harness refused the message before running it
    remember: dict = field(default_factory=dict)   # session fields to record (ids, limits)
    usage: dict = None              # one model call's normalized usage, for the ledger


class Harness:
    """One agent harness the dev sessions can run. Subclasses override what differs."""

    name = None
    label = None
    # True when the harness itself holds a message sent mid-run until the run ends (pi's
    # `followUp`). False makes the worker hold it, because the person wants every message delivered as its own turn
    # (2026-10-11) and a message absorbed into a running turn is lost.
    queues_followups = True
    # True when a model change reaches a running session; False means it applies at next start.
    live_model_switch = True

    def __init__(self, command=None):
        self.command = list(command or self.default_command())

    def default_command(self):
        raise NotImplementedError

    def start(self, rec, history, sessions_root=None):
        """`(argv, remember)`: the whole command line for this session's start, resuming its own
        conversation when there is one, and any session fields to record before it runs.
        `history` is the person's own messages so far, newest last."""
        raise NotImplementedError

    def after_start(self, rec, started, sessions_root=None):
        """Session fields learned shortly after a start (pi names its transcript only then)."""
        return {}

    def prompt(self, text, images=None):
        """The payload one message becomes. `images` is `[(mime, ext, bytes)]`."""
        raise NotImplementedError

    def steer(self, text, images=None):
        """The payload that injects one message into the run in progress, or None when this
        harness cannot steer safely. None means the message stays queued for its own turn —
        never merged into a running turn."""
        return None

    # The effort / thinking levels this harness accepts, in order; empty: none.
    efforts = []

    def set_effort(self, level):
        """The payload that changes a running session's level, or None when only a restart can."""
        return None

    def compact(self):
        """The payload that compacts the conversation, or None when this harness cannot."""
        return None

    def abort(self):
        """The payload that stops the run in progress (the session stays open), or None."""
        return None

    def set_model(self, model, provider=None):
        """The payload that switches a running session's model, or None if it cannot."""
        return None

    def fold(self, line, memo):
        """One raw line → a [Fold]. `memo` is this session's scratch dict for the worker's life
        (e.g. which tool a result belongs to); losing it on a restart costs only labels."""
        raise NotImplementedError

    def models(self):
        """The models this harness offers, for the Start form's dropdown."""
        return []

    def default_model(self):
        return None

    def describe(self):
        """The harness as the phone sees it (`GET /v1/dev/harnesses`)."""
        return {"name": self.name, "label": self.label, "models": self.models(),
                "default_model": self.default_model(), "live_model_switch": self.live_model_switch,
                "queues_followups": self.queues_followups, "efforts": list(self.efforts),
                "live_effort_switch": self.set_effort(self.efforts[0]) is not None if self.efforts else False}


def registry(commands=None):
    """Every harness the dev sessions can start, by name. `commands` replaces a harness's command
    line (tests run fakes); the order is the phone's dropdown order."""
    from .harness_pi import PiHarness
    from .harness_claude import ClaudeHarness
    commands = commands or {}
    out = {}
    for cls in (PiHarness, ClaudeHarness):
        out[cls.name] = cls(commands.get(cls.name))
    return out


def harness_for(harnesses, rec):
    """The harness a session runs; a session recorded before has none and ran pi."""
    name = (rec or {}).get("harness") or "pi"
    if name not in harnesses:
        raise ValueError("unknown harness: %s" % name)
    return harnesses[name]
