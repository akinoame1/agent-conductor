"""The session engine: wires the queue, the delivery wording and the run tracker over ports, so a host --
this server's worker, a terminal tool, anything -- supplies only storage, a transport to the harness, and its own
policy (spend caps, naming, pricing).

Ports the engine uses on its `store` (docs/conductor.md): get, set_state, set_field, append (returns the event with
its seq), held, hold_followup, queue_engine (a conductor.queue.Queue), queue_paused, begin_run, note_delivered,
flush_followup, mark_run_wake, load_run, save_run, clear_run, start_run, mark_read; and on its `transport`: send(sid, payload), raising one of [transport_errors] when the harness is
not there. The engine takes no locks: a host that is shared holds its own around each call.
"""
import secrets

from conductor import keepgoing, runs
from conductor.delivery import wrap


def every_words(seconds):
    return "%d min" % (seconds // 60) if seconds >= 60 else "%d s" % seconds


class Conductor:
    def __init__(self, store, transport, wrapper, transport_errors=(OSError,), usage=None, on_usage=None,
                 summary=None, on_run_end=None, blocked_on="the person you work for"):
        self.store = store
        self.transport = transport
        self.transport_errors = transport_errors
        # The host's own accounting, never the conductor's:
        # usage(sid, harness) -> fn(run, fold) adds its fields to the run; on_usage(sid, harness, fold) logs a
        # model call; summary(run, wall_s) is the run's closing line (None: no line); on_run_end(sid, run) after.
        self.usage = usage
        self.on_usage = on_usage
        self.summary = summary
        self.on_run_end = on_run_end
        # Who a wake says the agent may be blocked on ("end with NO MORE WORK if ... blocked on <them>").
        self.blocked_on = blocked_on
        # The words added to a held message (delivery.wrap): whoever runs the conductor chooses them.
        self.wrapper = wrapper

    def submit(self, sid, message, wire_text, harness, images=None, saved_images=None, shown_files=None,
               stored_files=None, answered=None):
        """The person's message: straight in when the session is idle, held while it works.
        [message] is his words, kept on the event; [wire_text] what the model gets (reply quote, files note).
        Returns "sent" or "queued"."""
        rec = self.store.get(sid) or {}
        # Idle with messages still held (a refusal, a flush that failed): drain one first. Otherwise this send
        # would itself be held, nothing would start a run, and the queue would deadlock.
        if self.store.held(sid) and rec.get("state") != "working":
            self.store.flush_followup(sid)
            rec = self.store.get(sid) or {}
        event = {"text": message}
        if saved_images:
            event["images"] = saved_images
        if shown_files:
            event["files"] = shown_files
        if answered is not None:
            event["in_reply_to"] = answered
        if rec.get("state") == "working":
            qid = secrets.token_hex(4)
            held = self.store.append(sid, dict(event, kind="queued", qid=qid))
            wrapped = wrap(wire_text, self.wrapper)
            self.store.hold_followup(sid, held["seq"], harness.prompt(wrapped, images), qid=qid, text=message,
                                     images=saved_images, in_reply_to=answered, delivered_as=wrapped,
                                     files=stored_files or None)
            return "queued"
        self.store.begin_run(sid)
        self.store.set_state(sid, "working")
        sent = self.store.append(sid, dict(event, kind="sent"))
        self.store.note_delivered(sid, sent["seq"])
        self.transport.send(sid, harness.prompt(wire_text, images))
        return "sent"

    def drain(self, sid, gate=None):
        """After a run: deliver the oldest held message as its own run. Nothing when none is held or the queue
        is stopped after a marked message. [gate](sid) may name a reason no run may start (a spend cap):
        then it stays queued and the log says why. Removed only once sent, so a failed send keeps it.
        True when a message went."""
        queue = self.store.queue_engine(sid)
        first = queue.next(paused=self.store.queue_paused(sid))
        if first is None:
            return False
        reason = gate(sid) if gate else None
        if reason:
            self.store.append(sid, {"kind": "error", "text": "queued message held: " + reason})
            return False
        try:
            self.transport.send(sid, first["payload"])
        except self.transport_errors:
            return False      # not running; it goes when the session next settles
        if queue.delivered(first.get("qid")):
            # Its run is the last that starts by itself: the next settle finds the queue paused.
            self.store.set_field(sid, "queue_paused", True)
        # The exact text the harness received, wrapper included, so a client can show it verbatim.
        event = {"kind": "delivered", "text": first.get("text", ""), "qid": first.get("qid"), "wrapped": True,
                 "delivered_as": first.get("delivered_as") or wrap(first.get("text", ""), self.wrapper)}
        if first.get("in_reply_to") is not None:
            event["in_reply_to"] = first["in_reply_to"]
        delivered = self.store.append(sid, event)
        self.store.begin_run(sid)
        # The delivered event's seq: a read receipt must reach the bubble the client draws.
        self.store.note_delivered(sid, delivered["seq"])
        self.store.set_state(sid, "working")
        return True

    # -- keep-going: wakes for a stalled long task -----------------------------------------------------

    def set_keep_going(self, sid, on, interval_s=None, cap=None, now=0.0):
        """Arm or disarm. Returns the stored state (None when off)."""
        if not on:
            self.keep_going_off(sid, "turned off")
            return None
        state = keepgoing.arm(interval_s if interval_s is not None else keepgoing.INTERVAL_S,
                              cap if cap is not None else keepgoing.CAP, now=now)
        self.store.set_field(sid, "keep_going", state)
        self.store.append(sid, {"kind": "keep_going", "text": "keep going armed: every %s, at most %d wakes"
                                % (every_words(state["interval_s"]), state["cap"])})
        return state

    def keep_going_off(self, sid, why):
        if keepgoing.read((self.store.get(sid) or {}).get("keep_going")) is None:
            return
        self.store.set_field(sid, "keep_going", None)
        self.store.append(sid, {"kind": "keep_going", "text": "keep going stopped: " + why})

    def keep_going_after(self, sid, rec, run, now):
        """A run settled: keep-going learns whether it is done, stuck or due again (or is armed by the run)."""
        if keepgoing.read(rec.get("keep_going")) is None and not keepgoing.ARM.search(run.get("text") or ""):
            return
        state, said = keepgoing.after_run(rec.get("keep_going"), {
            "tool_calls": run.get("tool_calls", 0), "wall_s": now - run.get("start", now),
            "wake": bool(run.get("wake")), "text": run.get("text")}, now=now)
        self.store.set_field(sid, "keep_going", state)
        rec["keep_going"] = state
        if said:
            self.store.append(sid, {"kind": "keep_going", "text": said})

    def _queue_clear(self, sid):
        # A paused queue's messages wait for Continue by choice; they do not hold back a wake.
        return not self.store.held(sid) or self.store.queue_paused(sid)

    def wake(self, sid, text, n, cap, harness):
        """Deliver a wake as a run of its own: a harness message, never the person's."""
        rec = self.store.get(sid) or {}
        if not self._queue_clear(sid) or rec.get("state") != "idle":
            return False
        try:
            self.transport.send(sid, harness.prompt(text))
        except self.transport_errors:
            return False
        self.store.append(sid, {"kind": "wake", "text": text, "n": n, "cap": cap})
        self.store.begin_run(sid)
        self.store.mark_run_wake(sid)
        self.store.set_state(sid, "working")
        return True

    def keep_going_tick(self, records, now, running, gate=None, harness_for=None, lock=None):
        """Fire every due wake over the sessions' [records]: idle, the queue clear, its time reached. A session
        not running, or a gate's reason (a spend cap), disarms it. True when one fired. [lock] is held around
        each wake only -- never across the tick: reading the records folds new output and drains queues, which
        takes the same lock, and a tick that held it deadlocked (found 2026-10-17, the keep-going tests hung)."""
        fired = False
        for rec in records:
            sid = rec.get("id")
            state = keepgoing.read(rec.get("keep_going"))
            if state is None or not keepgoing.due(state, now, rec.get("state") == "idle", self._queue_clear(sid)):
                continue
            if not running(sid):
                self.keep_going_off(sid, "the session is not running")
                continue
            reason = gate(sid) if gate else None
            if reason:
                self.keep_going_off(sid, reason)
                continue
            state, text, why = keepgoing.fire(state, self.blocked_on)
            if text is None:
                self.store.set_field(sid, "keep_going", None)
                self.store.append(sid, {"kind": "keep_going", "text": why})
                continue
            self.store.set_field(sid, "keep_going", state)
            if lock is not None:
                with lock:
                    fired = self.wake(sid, text, state["count"], state["cap"], harness_for(rec)) or fired
            else:
                fired = self.wake(sid, text, state["count"], state["cap"], harness_for(rec)) or fired
        return fired

    # -- the fold: one line of the harness's output ------------------------------------------------

    def fold_line(self, sid, rec, harness, line, memo, now):
        """One raw line: the run's counts, the events for the client, the settle and refusals.
        Returns (settled, refused): settled when the session went idle (the queue may drain), refused when the
        harness refused a message -- no green light for the queue, which waits for a real settle."""
        fold = harness.fold(line, memo)
        run = self.store.load_run(sid)
        if run is not None and runs.apply(run, fold, usage=self.usage(sid, harness) if self.usage else None):
            self.store.save_run(sid, run)
        if fold.call_started:
            # An LLM call began: the oldest delivered message has entered it (received -> read).
            self.store.mark_read(sid)
        if fold.usage and self.on_usage:
            self.on_usage(sid, harness, fold)
        for key, value in fold.remember.items():
            if value is not None and rec.get(key) != value:
                self.store.set_field(sid, key, value)
                rec[key] = value
        for event in fold.events:
            self.store.append(sid, event)
        if fold.settled:
            if run is not None:
                # The run's summary is a line of its own, and only for a run that did
                # something.
                if runs.worth_a_summary(run) and self.summary:
                    self.store.append(sid, {"kind": "cost", "text": self.summary(run, now - run["start"])})
                if runs.worth_a_summary(run) and self.on_run_end:
                    self.on_run_end(sid, run)
                self.store.clear_run(sid)
            then, nxt = runs.after_settle(run, now) if run is not None else ("idle", None)
            settled = False
            if then == "next":
                # The harness goes straight on to a message it held itself: a run of its own.
                self.store.start_run(sid, nxt)
            else:
                self.store.set_state(sid, "idle")
                settled = True
                if run is not None:
                    self.keep_going_after(sid, rec, run, now)
            if fold.refused is not None:
                # A settle that was also a refusal (Claude Code ends a failed run with an error result).
                self.store.append(sid, {"kind": "error", "text": "the harness refused that message: %s" % fold.refused})
                return settled, True
            return settled, False
        if fold.refused is not None:
            # Refused before it was taken: the "sent" line must not be the last word.
            self.store.append(sid, {"kind": "error", "text": "the harness refused that message: %s" % fold.refused})
            self.store.set_state(sid, "idle")
            return True, True
        return False, False
