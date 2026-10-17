"""A minimal terminal client: talk to pi or Claude Code through the conductor.

    python -m conductor.tui --harness claude --cwd ~/work/project [--wrapper "First line: what you will do."]

Type a message to send it (held while the agent works). Commands: /queue, /stop-after N, /continue,
/keep-going on [minutes] | off, /help, /quit. A session lives in a folder (--dir, default
<cwd>/.conductor/session) and survives restarts.
"""
import argparse
import os
import shlex
import sys
import threading
import time

HELP = ("/queue                 what is held, in delivery order\n"
        "/stop-after N          stop the queue after held message N (read its answer first)\n"
        "/continue              go on after a stop\n"
        "/keep-going on [min]   wake a stalled long task every [min] minutes (default 10)\n"
        "/keep-going off\n"
        "/quit")


def render(event):
    """One event as a printed line, or None for what a terminal need not show."""
    kind, text = event.get("kind"), (event.get("text") or "").strip()
    if kind == "text":
        return "agent: " + text
    if kind == "sent":
        return "you: " + text
    if kind == "delivered":
        return "you (from the queue): " + text
    if kind == "queued":
        return None
    if kind == "tool_call":
        return "  · " + text.splitlines()[0][:120] if text else None
    if kind in ("wake", "keep_going", "error", "cost"):
        return text or None
    return None


def handle(session, line):
    """What a typed line does; returns what to print."""
    line = line.strip()
    if not line:
        return ""
    if not line.startswith("/"):
        return "(queued — it delivers after this run)" if session.submit(line) == "queued" else ""
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    if cmd == "/help":
        return HELP
    if cmd == "/queue":
        q = session.queue()
        return "\n".join("%d. %s%s" % (i + 1, item["text"].splitlines()[0] if item["text"] else "",
                                       "  ⏸ stops after" if item.get("stop_after") else "")
                         for i, item in enumerate(q)) or "nothing held"
    if cmd == "/stop-after":
        q = session.queue()
        if not arg.isdigit() or not 1 <= int(arg) <= len(q):
            return "which one? /stop-after N, with N from /queue"
        session.stop_after(q[int(arg) - 1]["qid"], True)
        return "%s will stop the queue after it" % arg
    if cmd == "/continue":
        session.resume()
        return "continuing"
    if cmd == "/keep-going":
        what, _, minutes = arg.partition(" ")
        if what == "off":
            session.keep_going(False)
            return "keep going off"
        if what == "on":
            interval = int(minutes) * 60 if minutes.strip().isdigit() else None
            state = session.keep_going(True, interval_s=interval)
            return "keep going on" + (" (at most %d wakes)" % state["cap"] if state else "")
        return "/keep-going on [minutes] | off"
    return "unknown command; /help"


def main(argv=None):
    from conductor.harnesses import registry
    from conductor.host import Session
    p = argparse.ArgumentParser(prog="python -m conductor.tui")
    p.add_argument("--harness", choices=["pi", "claude"], default="claude")
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--dir", default=None, help="the session's folder (default <cwd>/.conductor/session)")
    p.add_argument("--wrapper", default="", help="text added to each held message on delivery")
    p.add_argument("--blocked-on", default="the person you work for")
    p.add_argument("--command", default=None, help="the harness's executable and fixed arguments, if not the default")
    a = p.parse_args(argv)
    cwd = os.path.abspath(os.path.expanduser(a.cwd))
    commands = {a.harness: shlex.split(a.command)} if a.command else None
    session = Session(a.dir or os.path.join(cwd, ".conductor", "session"), registry(commands)[a.harness], cwd=cwd,
                      wrapper=a.wrapper, blocked_on=a.blocked_on)
    session.start()
    seen = [e["seq"] for e in session.events()][-1:] or [0]
    last = {"seq": seen[0]}
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            session.pump()
            for e in session.events(last["seq"]):
                last["seq"] = e["seq"]
                line = render(e)
                if line:
                    print("\r" + line)
            time.sleep(0.25)

    threading.Thread(target=pump, daemon=True).start()
    print("conductor · %s in %s · /help" % (a.harness, cwd))
    try:
        for line in sys.stdin:
            if line.strip() == "/quit":
                break
            out = handle(session, line)
            if out:
                print(out)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        session.stop()


if __name__ == "__main__":
    main()
