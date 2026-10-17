"""A reference host: one session in a folder, the harness as a subprocess, the engine between them.

Enough to run the conductor without any server -- the terminal client (`python -m conductor.tui`) is built on
it -- and the shortest complete example of the ports in engine.py. A session folder holds:

    record.json       the session's fields (state, cwd, the harness's own ids, queue_paused, keep_going)
    events.jsonl      everything a client shows, one event per line, numbered
    followups.jsonl   the queue
    run.json          the run in progress
"""
import collections
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from conductor import runs
from conductor.engine import Conductor
from conductor.queue import FileQueueStore, Queue


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FileStore:
    """The engine's ports for one session, in its folder. `sid` is accepted and ignored: one folder, one session."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._seq = sum(1 for _ in self._lines("events.jsonl"))
        self.flush = lambda sid: False      # set by the Session: the engine's drain under its lock

    def _path(self, name):
        return os.path.join(self.root, name)

    def _lines(self, name):
        try:
            with open(self._path(name)) as fh:
                return [l for l in fh if l.strip()]
        except OSError:
            return []

    def _write_json(self, name, value):
        tmp = self._path(name + ".tmp")
        with open(tmp, "w") as fh:
            json.dump(value, fh)
        os.replace(tmp, self._path(name))

    # record
    def get(self, sid=None):
        try:
            with open(self._path("record.json")) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def set_field(self, sid, key, value):
        rec = self.get(sid)
        rec[key] = value
        self._write_json("record.json", rec)

    def set_state(self, sid, state):
        self.set_field(sid, "state", state)

    # events
    def append(self, sid, event):
        self._seq += 1
        e = dict(event, seq=self._seq)
        e.setdefault("at", _now_iso())
        with open(self._path("events.jsonl"), "a") as fh:
            fh.write(json.dumps(e) + "\n")
        return e

    def events_since(self, sid=None, after=0):
        out = []
        for line in self._lines("events.jsonl"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("seq", 0) > after:
                out.append(e)
        return out

    # queue
    def queue_engine(self, sid=None):
        return Queue(FileQueueStore(self._path("followups.jsonl")))

    def held(self, sid=None):
        return self.queue_engine().items()

    def hold_followup(self, sid, seq, payload, qid=None, text="", images=None, in_reply_to=None,
                      delivered_as=None, files=None):
        self.queue_engine().hold(qid, seq, payload, text=text, images=images, in_reply_to=in_reply_to,
                                 delivered_as=delivered_as, files=files)

    def queue_paused(self, sid=None):
        return bool(self.get().get("queue_paused"))

    def flush_followup(self, sid):
        return self.flush(sid)

    # runs
    def load_run(self, sid=None):
        try:
            with open(self._path("run.json")) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def save_run(self, sid, run):
        self._write_json("run.json", run)

    def start_run(self, sid, run):
        self.save_run(sid, run)

    def clear_run(self, sid):
        try:
            os.unlink(self._path("run.json"))
        except OSError:
            pass

    def begin_run(self, sid):
        self.save_run(sid, runs.new_run(time.time()))

    def mark_run_wake(self, sid):
        run = self.load_run(sid)
        if run is not None:
            run["wake"] = True
            self.save_run(sid, run)

    # read receipts: the newest message the model has taken up
    def note_delivered(self, sid, seq):
        self.set_field(sid, "delivered_seq", seq)

    def mark_read(self, sid):
        rec = self.get(sid)
        if rec.get("delivered_seq"):
            self.set_field(sid, "read_seq", rec["delivered_seq"])


class ProcessTransport:
    """The harness as a subprocess: payloads go to its stdin as JSON lines; its stdout is collected line by line."""

    def __init__(self):
        self.proc = None
        self._lines = collections.deque()

    def start(self, argv, cwd, log_path=None):
        err = open(log_path, "a") if log_path else subprocess.DEVNULL
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                     text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self._lines.append(line.rstrip("\n"))

    def running(self, sid=None):
        return self.proc is not None and self.proc.poll() is None

    def send(self, sid, payload):
        if not self.running():
            raise OSError("the harness is not running")
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def take_lines(self):
        out = []
        while self._lines:
            out.append(self._lines.popleft())
        return out

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Session:
    """One conversation with one harness. Call pump() regularly (the terminal client does, a few times a second):
    it folds the harness's new output, settles runs, drains the queue, and fires due keep-going wakes."""

    SID = "session"

    def __init__(self, root, harness, cwd, wrapper, blocked_on="the person you work for", gate=None):
        self.root = root
        self.harness = harness
        self.cwd = cwd
        self.store = FileStore(root)
        self.transport = ProcessTransport()
        self.gate = gate
        self.lock = threading.RLock()
        self.memo = {}
        self.engine = Conductor(self.store, self.transport, wrapper, blocked_on=blocked_on)
        self.store.flush = lambda sid: self.engine.drain(sid, gate=self.gate)

    def start(self):
        rec = self.store.get()
        rec.update(cwd=self.cwd, harness=self.harness.name)
        history = [e.get("text", "") for e in self.store.events_since() if e.get("kind") in ("sent", "delivered")]
        argv, remember = self.harness.start(rec, history)
        rec.update(remember)
        rec["state"] = "idle"
        self.store._write_json("record.json", rec)
        self.transport.start(argv, self.cwd, log_path=os.path.join(self.root, "harness.log"))

    def stop(self):
        self.transport.stop()
        self.store.set_state(self.SID, "stopped")

    def submit(self, text, reply_to=None):
        """Straight in when idle, held while it works. Returns "sent" or "queued"."""
        with self.lock:
            return self.engine.submit(self.SID, text, text, self.harness, answered=reply_to)

    def pump(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            for line in self.transport.take_lines():
                if not line.strip():
                    continue
                settled, refused = self.engine.fold_line(self.SID, self.store.get(), self.harness, line, self.memo, now)
                if settled and not refused:
                    self.engine.drain(self.SID, gate=self.gate)
            rec = dict(self.store.get(), id=self.SID)
            self.engine.keep_going_tick([rec], now, running=self.transport.running, gate=self.gate,
                                        harness_for=lambda r: self.harness)

    # what a client reads and does
    def events(self, after=0):
        return self.store.events_since(self.SID, after)

    def state(self):
        return self.store.get().get("state")

    def queue(self):
        return self.store.queue_engine().view()

    def stop_after(self, qid, on=True):
        with self.lock:
            return self.store.queue_engine().set_stop_after(qid, on)

    def resume(self):
        with self.lock:
            self.store.set_field(self.SID, "queue_paused", False)
            if self.state() == "idle":
                self.engine.drain(self.SID, gate=self.gate)

    def keep_going(self, on, interval_s=None, cap=None):
        with self.lock:
            return self.engine.set_keep_going(self.SID, on, interval_s, cap, now=time.time())
