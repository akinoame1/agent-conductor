"""The terminal client's two pure parts: how an event prints, and what a typed line does."""
import unittest

from conductor.tui import handle, render


class FakeSession:
    def __init__(self):
        self.sent, self.stops, self.resumed, self.kg = [], [], 0, None
        self.q = [{"qid": "a1", "text": "first held", "stop_after": False}, {"qid": "b2", "text": "second", "stop_after": True}]
    def submit(self, text): self.sent.append(text); return "queued"
    def queue(self): return self.q
    def stop_after(self, qid, on=True): self.stops.append((qid, on)); return True
    def resume(self): self.resumed += 1
    def keep_going(self, on, interval_s=None, cap=None): self.kg = (on, interval_s, cap); return {"cap": 12} if on else None


class TuiTest(unittest.TestCase):
    def test_events_print_as_short_lines(self):
        self.assertEqual("agent: hello", render({"kind": "text", "text": "hello"}))
        self.assertEqual("  · bash ls", render({"kind": "tool_call", "text": "bash ls"}))
        self.assertEqual("you: hi", render({"kind": "sent", "text": "hi"}))
        self.assertEqual("you (from the queue): hi", render({"kind": "delivered", "text": "hi"}))
        self.assertEqual("[wake 1/12] continue", render({"kind": "wake", "text": "[wake 1/12] continue"}))
        self.assertIsNone(render({"kind": "tool_result", "text": "a.txt"}))

    def test_a_plain_line_is_a_message(self):
        s = FakeSession()
        self.assertEqual("(queued — it delivers after this run)", handle(s, "fix the test"))
        self.assertEqual(["fix the test"], s.sent)

    def test_queue_commands(self):
        s = FakeSession()
        self.assertEqual("1. first held\n2. second  ⏸ stops after", handle(s, "/queue"))
        self.assertEqual("1 will stop the queue after it", handle(s, "/stop-after 1"))
        self.assertEqual([("a1", True)], s.stops)
        self.assertEqual("continuing", handle(s, "/continue"))
        self.assertEqual(1, s.resumed)

    def test_keep_going_and_help(self):
        s = FakeSession()
        handle(s, "/keep-going on 5")
        self.assertEqual((True, 300, None), s.kg)
        handle(s, "/keep-going off")
        self.assertEqual(False, s.kg[0])
        self.assertIn("/queue", handle(s, "/help"))
        self.assertIn("unknown", handle(s, "/nope"))


if __name__ == "__main__":
    unittest.main()
