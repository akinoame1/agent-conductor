"""Catching up on what the agent did, and finding your own messages in a long session.

The rules behind the phone client's concise view and its step-to-my-message aids (app_infra#423, #349, #428),
here so that any client can use them (app_infra#469). They read the conductor's events
({"seq", "kind", "text"}) and draw nothing.

A pair is one of your messages with the turn's **last** answer before your next message. The first answer is
usually the acknowledgement ("Understood: … acting now", which delivery instructions ask for); the substance
comes at the end, so do not change this to the first answer.
"""
import re
from collections import namedtuple

MINE = ("sent", "delivered")     # "queued" is only the echo of a held message; its delivery is the message
ANSWER = ("text",)

# first: the turn's first answer when it is not the last -- often the diagnosis, the agent's reading of the problem
# (akinoame1/app_infra#519); None when the turn answered once.
Pair = namedtuple("Pair", "mine answer tool_calls first")


def pairs(events):
    """Each of your messages with the turn's last answer (None while there is none), its tool calls, and the turn's
    first answer when that is a different one."""
    out, mine, answer, first, tools = [], None, None, None, 0

    def close():
        if mine is not None:
            out.append(Pair(mine, answer, tools, first if first is not answer else None))

    for e in events:
        kind = e.get("kind")
        if kind in MINE:
            close()
            mine, answer, first, tools = e, None, None, 0
        elif mine is not None:
            if kind in ANSWER:
                first = first or e
                answer = e
            elif kind == "tool_call":
                tools += 1
    close()
    return out

def first_paragraph(text):
    """(the text's first paragraph, whether more follows it)."""
    t = (text or "").strip()
    cut = t.find("\n\n")
    return (t, False) if cut < 0 else (t[:cut].strip(), True)


_REF = re.compile(r"#(\d{2,5})\b")
_VERSION = re.compile(r"\b1\.0\.\d+\b")


def _distinct(items, n):
    seen = []
    for i in items:
        if i not in seen:
            seen.append(i)
    return seen[:n]


def digest(pair):
    """What the turn did, in one line: its tool calls, the issues and PRs its answer names, the versions it names."""
    text = (pair.answer or {}).get("text") or ""
    parts = []
    if pair.tool_calls:
        parts.append("%d tool call%s" % (pair.tool_calls, "" if pair.tool_calls == 1 else "s"))
    refs = _distinct(("#" + m for m in _REF.findall(text)), 5)
    if refs:
        parts.append(" ".join(refs))
    versions = _distinct(_VERSION.findall(text), 2)
    if versions:
        parts.append(" ".join(versions))
    return " · ".join(parts)


def catch_up(events, after_seq):
    """The pairs with something you have not seen: answered (or, unanswered, sent) after `after_seq`."""
    return [p for p in pairs(events) if ((p.answer or p.mine).get("seq") or 0) > after_seq]


def mine_seqs(events):
    """The seqs of your messages, in order."""
    return [e["seq"] for e in events if e.get("kind") in MINE]


def step_counts(mine, before_seq, after_seq):
    """(how many of your messages are above `before_seq`, how many below `after_seq`), over the whole session."""
    return sum(1 for s in mine if s < before_seq), sum(1 for s in mine if s > after_seq)


def step(mine, at_seq, back):
    """Your previous (back) or next message from `at_seq`, or None when there is none."""
    if back:
        return max((s for s in mine if s < at_seq), default=None)
    return min((s for s in mine if s > at_seq), default=None)
