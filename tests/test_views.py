"""Catching up and finding your own messages: the rules of the phone's concise view (app_infra#423) and its
step-to-my-message aids (#349, #428), moved here so every client has them (app_infra#469).

Denis, 2026-10-17: "Lets think about concise chat view … keep my messages and yours immediately near it";
"Scroll to prev next my msg: add counts, how many prev next are avail, in the whole chat"."""
import unittest

from conductor import views

CHAT = [
    {"seq": 1, "kind": "sent", "text": "fix the flicker"},
    {"seq": 2, "kind": "text", "text": "Understood: a bug report. Acting now."},
    {"seq": 3, "kind": "tool_call", "text": "bash ls"},
    {"seq": 4, "kind": "tool_result", "text": "a.txt"},
    {"seq": 5, "kind": "tool_call", "text": "bash git push"},
    {"seq": 6, "kind": "text", "text": "Fixed: the follow jumped to the top (#432, PR #433). 1.0.270 is out.\n\nDetails: the offset overflowed."},
    {"seq": 7, "kind": "cost", "text": "2 LLM calls · 2 tool calls · 30s"},
    {"seq": 8, "kind": "queued", "text": "and the queue?"},
    {"seq": 9, "kind": "delivered", "text": "and the queue?"},
    {"seq": 10, "kind": "text", "text": "Queue panel shipped."},
    {"seq": 11, "kind": "sent", "text": "thanks"},
]


class PairsTest(unittest.TestCase):
    def test_each_of_your_messages_pairs_with_the_turns_last_answer(self):
        # The first answer is the acknowledgement ("Understood … acting now"); the substance comes last.
        pairs = views.pairs(CHAT)
        self.assertEqual([1, 9, 11], [p.mine["seq"] for p in pairs])
        self.assertEqual([6, 10, None], [p.answer["seq"] if p.answer else None for p in pairs])
        self.assertEqual(2, pairs[0].tool_calls)

    def test_a_queued_echo_is_not_a_message_of_yours_its_delivery_is(self):
        self.assertNotIn(8, [p.mine["seq"] for p in views.pairs(CHAT)])

    def test_the_answer_shows_its_first_paragraph_and_the_digest_what_happened(self):
        self.assertEqual(("Fixed: the follow jumped to the top (#432, PR #433). 1.0.270 is out.", True),
                         views.first_paragraph(CHAT[5]["text"]))
        self.assertEqual(("Queue panel shipped.", False), views.first_paragraph("Queue panel shipped."))
        self.assertEqual("2 tool calls · #432 #433 · 1.0.270", views.digest(views.pairs(CHAT)[0]))
        self.assertEqual("", views.digest(views.pairs(CHAT)[2]))

    def test_catching_up_starts_at_the_first_pair_answered_after_what_you_saw(self):
        self.assertEqual([9, 11], [p.mine["seq"] for p in views.catch_up(CHAT, after_seq=6)])
        self.assertEqual([1, 9, 11], [p.mine["seq"] for p in views.catch_up(CHAT, after_seq=0)])
        self.assertEqual([], views.catch_up(CHAT, after_seq=11))


class MineTest(unittest.TestCase):
    def test_your_messages_are_the_sent_and_the_delivered(self):
        self.assertEqual([1, 9, 11], views.mine_seqs(CHAT))

    def test_counts_are_over_the_whole_chat(self):
        mine = [10, 20, 101, 103, 105]
        self.assertEqual((3, 1), views.step_counts(mine, before_seq=102, after_seq=104))
        self.assertEqual((0, 5), views.step_counts(mine, before_seq=5, after_seq=5))

    def test_a_step_goes_to_your_previous_or_next_message_or_nowhere(self):
        mine = [10, 20, 101]
        self.assertEqual(20, views.step(mine, at_seq=101, back=True))
        self.assertEqual(101, views.step(mine, at_seq=50, back=False))
        self.assertIsNone(views.step(mine, at_seq=10, back=True))
        self.assertIsNone(views.step(mine, at_seq=101, back=False))


if __name__ == "__main__":
    unittest.main()
