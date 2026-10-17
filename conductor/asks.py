"""Asks: what an agent needs from its person, kept per session like the queue, in the other direction.

From the phone host this library grew in (akinoame1/app_infra#489): its person had a queue of his requests to the
agent, and no list of the agent's requests to him. Pure functions over a list of asks; the host stores the list.

An ask is raised by what the agent writes, never guessed from tone:
- a line `ASK: <what>`;
- a `NO MORE WORK: <why>` that names the person: it is blocked on them;
- a closing question (the answer's last line ends in "?"), when no ASK line said it.
It closes three ways: the person's reply quoting its message (any next message closes a closing question,
which it answers), the agent writing `RESOLVED: <id>`, or the person dismissing it. Closed asks are kept.
"""
import re

ASK = re.compile(r"^\s*ASK\b:\s*(.+)$", re.M)
RESOLVED = re.compile(r"^\s*RESOLVED\b:?\s*(.+)$", re.M)
_DONE = re.compile(r"^\s*NO MORE WORK\b:?\s*(.*)$", re.M)
_ID = re.compile(r"\ba\d+\b")
PERSON = None          # the person's name, when known; "you" always counts


def raised(text, seq, person=PERSON):
    """The asks one answer raises: [{source, text, seq}] in the order written."""
    text = text or ""
    out = [{"source": "ask", "text": m.strip(), "seq": seq} for m in ASK.findall(text)]
    done = _DONE.search(text)
    names = r"\b(you|your%s)\b" % ("|" + re.escape(person) if person else "")
    if done and re.search(names, done.group(1), re.I):
        out.append({"source": "blocked", "text": done.group(1).strip(), "seq": seq})
    if not out:
        lines = [l.strip().strip("*_ ").strip() for l in text.strip().splitlines() if l.strip()]
        if lines and "?" in lines[-1] and not _DONE.match(lines[-1]):
            last = lines[-1]
            if last.endswith("?") or re.search(r"\?\s+\S", last):
                out.append({"source": "question", "text": last, "seq": seq})
    return out


def _next_id(book):
    return "a%d" % (1 + max((int(a["id"][1:]) for a in book), default=0))


def _close(a, state, by, seq, at):
    a.update(state=state, closed_by=by, closed_seq=seq, closed_at=at)


def open_(book):
    return [a for a in book if a.get("state") == "open"]


def after_answer(book, text, seq, at, person=PERSON):
    """An answer settled: its RESOLVED lines close asks by id, then what it raises is added."""
    book = [dict(a) for a in book]
    gone = set()
    for line in RESOLVED.findall(text or ""):
        gone.update(_ID.findall(line))
    for a in open_(book):
        if a["id"] in gone:
            _close(a, "resolved", "agent", seq, at)
    for a in raised(text, seq, person):
        book.append(dict(a, id=_next_id(book), state="open", at=at))
    return book


def after_person(book, seq, in_reply_to, at):
    """The person wrote: a reply quoting an ask's message closes it; any message answers an open closing question."""
    book = [dict(a) for a in book]
    for a in open_(book):
        if (in_reply_to is not None and a["seq"] == in_reply_to) or a["source"] == "question":
            _close(a, "answered", "person", seq, at)
    return book


def dismiss(book, aid, at):
    """(book, whether an open ask had that id)."""
    book = [dict(a) for a in book]
    for a in open_(book):
        if a["id"] == aid:
            _close(a, "dismissed", "person", None, at)
            return book, True
    return book, False


def note(book):
    """Added to a delivered message while asks are open, so the agent knows what it still waits on and how to
    close one; "" when none are open."""
    o = [a for a in open_(book) if a["source"] != "question"]
    if not o:
        return ""
    return ("\n\n[Your open asks: " + "; ".join("%s: %s" % (a["id"], a["text"]) for a in o) +
            ". When one is no longer needed, write a line `RESOLVED: %s`.]" % o[0]["id"])
