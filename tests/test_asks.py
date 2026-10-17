"""Asks: what the agent needs from its person, kept per session like the queue in the other direction
(akinoame1/app_infra#489)."""
import unittest

from conductor import asks


class RaiseTest(unittest.TestCase):
    def test_ask_lines_each_raise_one(self):
        got = asks.raised("Done with the build.\nASK: add the GitHub token\nASK: test it on the phone", seq=40)
        self.assertEqual([("ask", "add the GitHub token", 40), ("ask", "test it on the phone", 40)],
                         [(a["source"], a["text"], a["seq"]) for a in got])

    def test_a_stop_that_names_the_person_is_an_ask_waiting_on_ci_is_not(self):
        self.assertEqual(["blocked"], [a["source"] for a in asks.raised("NO MORE WORK: blocked on Dana for the key", 7, person="Dana")])
        self.assertEqual([], asks.raised("NO MORE WORK: waiting on CI for PR #458", 7))
        self.assertEqual([], asks.raised("NO MORE WORK: all merged", 7))

    def test_a_closing_question_is_an_ask_unless_an_ask_line_said_it(self):
        self.assertEqual([("question", "Which ones should I do? I'd start with 1 and 2.")],
                         [(a["source"], a["text"]) for a in asks.raised("Five ideas.\n\nWhich ones should I do? I'd start with 1 and 2.", 9)])
        self.assertEqual([("question", "Merge it now?")], [(a["source"], a["text"]) for a in asks.raised("All green.\n\n**Merge it now?**", 9)])
        self.assertEqual(["ask"], [a["source"] for a in asks.raised("ASK: the token\n\nShall I go on?", 9)])
        self.assertEqual([], asks.raised("Shipped 1.0.275.", 9))

    def test_the_format_quoted_mid_line_raises_nothing(self):
        self.assertEqual([], asks.raised("Put it on a line `ASK: <what>` and I will see it.", 3))


class LifeTest(unittest.TestCase):
    def test_ids_go_on_across_answers_and_the_agent_resolves_by_id(self):
        book = asks.after_answer([], "ASK: the token", seq=10, at="t1")
        book = asks.after_answer(book, "ASK: a phone test", seq=20, at="t2")
        self.assertEqual(["a1", "a2"], [a["id"] for a in asks.open_(book)])
        book = asks.after_answer(book, "Found it in the vault.\nRESOLVED: a1", seq=30, at="t3")
        self.assertEqual(["a2"], [a["id"] for a in asks.open_(book)])
        closed = [a for a in book if a["id"] == "a1"][0]
        self.assertEqual(("resolved", "agent", 30), (closed["state"], closed["closed_by"], closed["closed_seq"]))

    def test_a_reply_quoting_the_ask_closes_it_and_a_plain_message_closes_only_questions(self):
        book = asks.after_answer([], "ASK: the token", seq=10, at="t")
        book = asks.after_answer(book, "Shall I merge?", seq=12, at="t")
        book = asks.after_person(book, seq=13, in_reply_to=None, at="t")
        self.assertEqual(["a1"], [a["id"] for a in asks.open_(book)])      # the question was answered, the token not
        book = asks.after_person(book, seq=14, in_reply_to=10, at="t")
        self.assertEqual([], asks.open_(book))
        self.assertEqual("person", [a for a in book if a["id"] == "a1"][0]["closed_by"])

    def test_dismiss_and_closed_asks_are_kept(self):
        book = asks.after_answer([], "ASK: the token", seq=10, at="t")
        book, ok = asks.dismiss(book, "a1", at="t")
        self.assertTrue(ok)
        self.assertEqual([], asks.open_(book))
        self.assertEqual(["dismissed"], [a["state"] for a in book])
        self.assertFalse(asks.dismiss(book, "a9", at="t")[1])

    def test_a_delivered_message_lists_the_open_asks_and_how_to_resolve_one(self):
        self.assertEqual("", asks.note([]))
        book = asks.after_answer([], "ASK: the token", seq=10, at="t")
        note = asks.note(book)
        self.assertIn("a1: the token", note)
        self.assertIn("RESOLVED: a1", note)


if __name__ == "__main__":
    unittest.main()


class PersonTest(unittest.TestCase):
    def test_without_a_name_only_you_counts_as_the_person(self):
        self.assertEqual(["blocked"], [a["source"] for a in asks.raised("NO MORE WORK: waiting on you to choose", 1)])
        self.assertEqual([], asks.raised("NO MORE WORK: blocked on Dana", 1))
