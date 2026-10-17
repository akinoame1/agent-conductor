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
    def events(self, after=0): return [e for e in CHAT if e["seq"] > after]
    def asks(self): return [{"id": "a1", "source": "ask", "text": "add the token", "seq": 4}] if not self.dismissed else []
    dismissed = False
    def dismiss_ask(self, aid): self.dismissed = aid == "a1"; return self.dismissed


CHAT = [
    {"seq": 1, "kind": "sent", "text": "fix the flicker"},
    {"seq": 2, "kind": "text", "text": "Understood. Acting now."},
    {"seq": 3, "kind": "tool_call", "text": "bash ls"},
    {"seq": 4, "kind": "text", "text": "Fixed (#432).\n\nDetails follow."},
    {"seq": 5, "kind": "sent", "text": "and the queue?\nsecond line"},
    {"seq": 6, "kind": "text", "text": "Queue panel shipped."},
]


class ViewsTest(unittest.TestCase):
    """/catchup and /mine (app_infra#469): the phone's concise view and my-message jumps, in a terminal."""

    def test_catchup_prints_each_of_your_messages_with_the_last_answer_and_then_only_whats_new(self):
        s, marks = FakeSession(), {}
        self.assertEqual("you: fix the flicker\n  1 tool call · #432\nagent: Fixed (#432). …\n\n"
                         "you: and the queue?\nagent: Queue panel shipped.", handle(s, "/catchup", marks))
        self.assertEqual("nothing new since the last /catchup", handle(s, "/catchup", marks))

    def test_mine_lists_your_messages_and_opens_one_with_its_answer(self):
        s = FakeSession()
        self.assertEqual("2 of yours\n1. fix the flicker\n2. and the queue?", handle(s, "/mine"))
        self.assertEqual("you: and the queue?\nsecond line\nagent: Queue panel shipped.\n(1 before, 0 after)",
                         handle(s, "/mine 2"))
        self.assertEqual("which one? /mine N, with N from /mine", handle(s, "/mine 3"))


class AsksTuiTest(unittest.TestCase):
    """/asks, /reply and /dismiss (akinoame1/app_infra#489)."""

    def test_asks_list_and_reply_quotes_the_message_and_dismiss_closes(self):
        s = FakeSession()
        s.replies = []
        s.submit = lambda text, reply_to=None: s.replies.append((text, reply_to)) or "sent"
        self.assertEqual("a1 · needs you · add the token", handle(s, "/asks"))
        self.assertEqual("", handle(s, "/reply a1 it is in the vault"))
        self.assertEqual([("it is in the vault", 4)], s.replies)
        self.assertEqual("which one? /reply aN <text>, with aN from /asks", handle(s, "/reply a9 x"))
        self.assertEqual("a1 dismissed", handle(s, "/dismiss a1"))
        self.assertEqual("nothing asked", handle(s, "/asks"))


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
