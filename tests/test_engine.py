"""The session engine's send: straight in when the agent is idle, held — visible, editable, wrapped —
while it works, and the queue drained first when the session is idle with messages still held, which once
deadlocked the queue. Tested over in-memory ports, no harness process."""
import unittest

from conductor.engine import Conductor
from conductor.queue import MemoryQueueStore, Queue


class FakeHarness:
    name = "fake"

    def prompt(self, text, images=None):
        return {"type": "prompt", "message": text, "images": images}


class FakeStore:
    """The ports the engine uses, in memory."""
    def __init__(self, state="idle", transport=None):
        self.rec = {"state": state}
        self.transport = transport
        self.events, self.runs, self.read = [], 0, []
        self.q = Queue(MemoryQueueStore())

    def get(self, sid): return dict(self.rec)
    def set_state(self, sid, state): self.rec["state"] = state
    def append(self, sid, event):
        e = dict(event, seq=len(self.events) + 1); self.events.append(e); return e
    def held(self, sid): return self.q.items()
    def hold_followup(self, sid, seq, payload, **kw): self.q.hold(kw.pop("qid"), seq, payload, **kw)
    def begin_run(self, sid): self.runs += 1
    def note_delivered(self, sid, seq): self.read.append(seq)
    def flush_followup(self, sid): return Conductor(self, self.transport, wrapper="W").drain(sid)
    def queue_engine(self, sid): return self.q
    def queue_paused(self, sid): return bool(self.rec.get("queue_paused"))
    def set_field(self, sid, key, value): self.rec[key] = value
    def mark_run_wake(self, sid): self.rec["run_wake"] = True
    run = None
    def load_run(self, sid): return self.run
    def save_run(self, sid, run): self.run = run
    def clear_run(self, sid): self.run = None
    def start_run(self, sid, run): self.run = run
    def mark_read(self, sid): self.read.append("read")


class FakeTransport:
    def __init__(self): self.sent = []
    def send(self, sid, payload): self.sent.append(payload)


class EngineSendTest(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()

    def engine(self, store):
        store.transport = self.transport
        return Conductor(store, self.transport, wrapper="SAY WHAT YOU WILL DO")

    def test_an_idle_session_takes_the_message_straight_in(self):
        store = FakeStore("idle")
        self.engine(store).submit("s", "hello", "hello", FakeHarness())
        self.assertEqual(["hello"], [p["message"] for p in self.transport.sent])
        self.assertEqual(("sent", "working", 1, [1]), (store.events[0]["kind"], store.rec["state"], store.runs, store.read))

    def test_a_working_session_holds_it_wrapped(self):
        store = FakeStore("working")
        self.engine(store).submit("s", "and this", "and this", FakeHarness(), answered=4)
        self.assertEqual([], self.transport.sent)
        q = store.q.items()[0]
        self.assertEqual("and this\n\nSAY WHAT YOU WILL DO", q["payload"]["message"])
        self.assertEqual("and this\n\nSAY WHAT YOU WILL DO", q["delivered_as"])
        self.assertEqual(("queued", 4), (store.events[0]["kind"], store.events[0]["in_reply_to"]))

    def test_idle_with_messages_held_drains_first_then_holds_the_new_one(self):
        store = FakeStore("idle")
        store.q.hold("old", 1, {"message": "OLD"}, text="OLD")
        self.engine(store).submit("s", "new", "new", FakeHarness())
        self.assertEqual(["new"], [q["text"] for q in store.q.items()])     # held behind the drained one
        self.assertEqual("queued", store.events[-1]["kind"])

    def test_the_event_keeps_his_words_and_the_wire_gets_the_full_text(self):
        store = FakeStore("idle")
        self.engine(store).submit("s", "my words", "(Replying to …)\n\nmy words", FakeHarness(),
                                  saved_images=[{"name": "1.jpg"}], shown_files=[{"name": "cv.pdf"}])
        e = store.events[0]
        self.assertEqual(("my words", [{"name": "1.jpg"}], [{"name": "cv.pdf"}]), (e["text"], e["images"], e["files"]))
        self.assertEqual("(Replying to …)\n\nmy words", self.transport.sent[0]["message"])



class FailingTransport:
    def send(self, sid, payload): raise OSError("not running")


class EngineDrainTest(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.store = FakeStore("idle", self.transport)
        for qid in ("a", "b"):
            self.store.q.hold(qid, 1, {"message": qid.upper() + " wrapped"}, text=qid.upper(),
                              delivered_as=qid.upper() + " wrapped", in_reply_to=3 if qid == "a" else None)
        self.c = Conductor(self.store, self.transport, wrapper="W")

    def test_the_drain_delivers_the_oldest_as_its_own_run(self):
        self.assertTrue(self.c.drain("s"))
        self.assertEqual(["A wrapped"], [p["message"] for p in self.transport.sent])
        e = self.store.events[-1]
        self.assertEqual(("delivered", "A", "A wrapped", 3), (e["kind"], e["text"], e["delivered_as"], e["in_reply_to"]))
        self.assertEqual(("working", 1, [e["seq"]]), (self.store.rec["state"], self.store.runs, self.store.read))
        self.assertEqual(["b"], [q["qid"] for q in self.store.q.items()])

    def test_a_gate_holds_it_and_says_why(self):
        self.assertFalse(self.c.drain("s", gate=lambda sid: "the daily cap"))
        self.assertEqual([], self.transport.sent)
        self.assertEqual("queued message held: the daily cap", self.store.events[-1]["text"])
        self.assertEqual(2, len(self.store.q.items()))

    def test_a_failed_send_keeps_the_message(self):
        c = Conductor(self.store, FailingTransport(), wrapper="W")
        self.assertFalse(c.drain("s"))
        self.assertEqual(2, len(self.store.q.items()))

    def test_stop_after_pauses_and_a_paused_queue_delivers_nothing(self):
        self.store.q.set_stop_after("a", True)
        self.c.drain("s")
        self.assertTrue(self.store.rec["queue_paused"])
        self.assertFalse(self.c.drain("s"))
        self.assertEqual(1, len(self.transport.sent))



class EngineKeepGoingTest(unittest.TestCase):
    """Keep-going in the engine."""
    def setUp(self):
        from conductor import keepgoing
        self.kg = keepgoing
        self.transport = FakeTransport()
        self.store = FakeStore("idle", self.transport)
        self.c = Conductor(self.store, self.transport, wrapper="W")
        self.store.rec.update(id="s", keep_going=keepgoing.arm(interval_s=0, cap=2, now=0))

    def tick(self, running=True, gate=None):
        return self.c.keep_going_tick([dict(self.store.rec)], now=10, running=lambda sid: running,
                                      gate=gate, harness_for=lambda rec: FakeHarness())

    def test_a_due_idle_session_is_woken_with_a_marked_harness_message(self):
        self.assertTrue(self.tick())
        e = self.store.events[-1]
        self.assertEqual(("wake", 1, 2), (e["kind"], e["n"], e["cap"]))
        self.assertIn("[wake 1/2]", self.transport.sent[0]["message"])
        self.assertEqual(("working", True), (self.store.rec["state"], self.store.rec.get("run_wake")))

    def test_held_messages_hold_it_back_but_a_paused_queue_does_not(self):
        self.store.q.hold("x", 1, {"message": "X"}, text="X")
        self.assertFalse(self.tick())
        self.store.rec["queue_paused"] = True
        self.assertTrue(self.tick())
        self.assertEqual(["X"], [q["text"] for q in self.store.q.items()])

    def test_a_stopped_session_or_a_gate_disarms_with_the_reason(self):
        self.assertFalse(self.tick(running=False))
        self.assertIsNone(self.store.rec["keep_going"])
        self.assertIn("not running", self.store.events[-1]["text"])
        self.store.rec["keep_going"] = self.kg.arm(interval_s=0, cap=2, now=0)
        self.assertFalse(self.tick(gate=lambda sid: "the daily cap"))
        self.assertIn("the daily cap", self.store.events[-1]["text"])

    def test_after_a_run_the_done_line_disarms(self):
        self.c.keep_going_after("s", self.store.get("s"), {"tool_calls": 0, "start": 0, "text": "ok\nNO MORE WORK: shipped"}, now=5)
        self.assertIsNone(self.store.rec["keep_going"])
        self.assertIn("shipped", self.store.events[-1]["text"])

    def test_arm_and_disarm(self):
        state = self.c.set_keep_going("s", True, interval_s=300, cap=4, now=0)
        self.assertEqual((300, 4), (state["interval_s"], state["cap"]))
        self.assertIn("armed", self.store.events[-1]["text"])
        self.assertIsNone(self.c.set_keep_going("s", False))
        self.assertIn("turned off", self.store.events[-1]["text"])



class ScriptedHarness(FakeHarness):
    """Each raw line names a prepared Fold."""
    def __init__(self, folds): self.folds = folds
    def fold(self, line, memo): return self.folds[line]


class EngineFoldTest(unittest.TestCase):
    """The per-line fold in the engine: run counts, events, the settle, refusals; pricing by hooks."""
    def setUp(self):
        from conductor.harnesses import Fold
        from conductor.runs import new_run
        self.Fold = Fold
        self.store = FakeStore("working", FakeTransport())
        self.store.run = new_run(now=0)
        self.summaries, self.ends, self.usage_log = [], [], []
        self.c = Conductor(self.store, self.store.transport, wrapper="W",
                           summary=lambda run, wall: "%d tools in %ds" % (run["tool_calls"], wall),
                           on_run_end=lambda sid, run: self.ends.append(run["tool_calls"]),
                           on_usage=lambda sid, harness, fold: self.usage_log.append(fold.usage))

    def fold(self, **folds):
        h = ScriptedHarness({k: self.Fold(**v) for k, v in folds.items()})
        return [self.c.fold_line("s", self.store.get("s"), h, line, {}, now=30) for line in folds]

    def test_a_run_counts_says_its_events_and_settles_with_a_summary(self):
        out = self.fold(a={"tool_calls": 2, "call_started": True, "events": [{"kind": "tool_call", "text": "ls"}]},
                        b={"usage": {"tokens": 5}, "remember": {"model": "m-two"}},
                        c={"settled": True, "events": [{"kind": "text", "role": "assistant", "text": "done"}]})
        self.assertEqual([(False, False), (False, False), (True, False)], out)
        kinds = [e["kind"] for e in self.store.events]
        self.assertEqual(["tool_call", "text", "cost"], kinds)
        self.assertEqual("2 tools in 30s", self.store.events[-1]["text"])
        self.assertEqual(("idle", "m-two", None), (self.store.rec["state"], self.store.rec["model"], self.store.run))
        self.assertEqual(([2], [{"tokens": 5}], ["read"]), (self.ends, self.usage_log, self.store.read))

    def test_a_run_that_did_nothing_settles_without_a_summary(self):
        self.fold(a={"settled": True})
        self.assertEqual([], [e for e in self.store.events if e["kind"] == "cost"])

    def test_the_harness_going_on_to_a_held_message_starts_the_next_run(self):
        self.store.run["queued"] = 1
        self.assertEqual([(False, False)], self.fold(a={"settled": True}))
        self.assertEqual(("working", 0), (self.store.rec["state"], self.store.run.get("queued", 0)))

    def test_a_refusal_is_said_and_is_no_green_light(self):
        self.assertEqual([(True, True)], self.fold(a={"refused": "Model not found"}))
        self.assertEqual(("error", "idle"), (self.store.events[-1]["kind"], self.store.rec["state"]))
        self.assertIn("Model not found", self.store.events[-1]["text"])


if __name__ == "__main__":
    unittest.main()
