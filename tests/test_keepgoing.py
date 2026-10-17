"""Keep-going wakes for a stalled long task: when they are due, and every way they disarm."""
import unittest

from conductor import keepgoing as kg


class LogicTest(unittest.TestCase):
    def run_(self, tools=0, wall=10.0, wake=False, text="ok"):
        return {"tool_calls": tools, "wall_s": wall, "wake": wake, "text": text}

    def test_off_by_default_and_an_unreadable_setting_is_off(self):
        self.assertIsNone(kg.read(None))
        self.assertIsNone(kg.read("yes"))
        self.assertIsNone(kg.read({"interval_s": "ten"}))
        self.assertEqual(600, kg.read(kg.arm())["interval_s"])
        self.assertEqual(12, kg.read(kg.arm())["cap"])

    def test_a_significant_run_makes_it_due_at_once_a_small_one_after_the_interval(self):
        state, _ = kg.after_run(kg.arm(), self.run_(tools=5), now=1000)
        self.assertEqual(1000, state["next_at"])
        state, _ = kg.after_run(kg.arm(), self.run_(wall=200), now=1000)
        self.assertEqual(1000, state["next_at"])
        state, _ = kg.after_run(kg.arm(), self.run_(tools=1), now=1000)
        self.assertEqual(1600, state["next_at"])

    def test_due_only_when_idle_and_the_real_queue_is_empty(self):
        state, _ = kg.after_run(kg.arm(), self.run_(tools=9), now=1000)
        self.assertTrue(kg.due(state, now=1000, idle=True, queue_empty=True))
        self.assertFalse(kg.due(state, now=1000, idle=False, queue_empty=True))
        self.assertFalse(kg.due(state, now=1000, idle=True, queue_empty=False))
        self.assertFalse(kg.due(None, now=1000, idle=True, queue_empty=True))

    def test_the_done_line_disarms_with_its_reason(self):
        state, why = kg.after_run(kg.arm(), self.run_(text="Shipped.\nNO MORE WORK: all three PRs merged"), now=1)
        self.assertIsNone(state)
        self.assertIn("all three PRs merged", why)

    def test_the_done_phrase_quoted_mid_line_does_not_disarm(self):
        state, why = kg.after_run(kg.arm(), self.run_(text="I will write `NO MORE WORK: <why>` when done"), now=1)
        self.assertIsNotNone(state)
        self.assertIsNone(why)

    def test_two_wakes_in_a_row_without_a_tool_call_disarm(self):
        state = kg.arm()
        state, why = kg.after_run(state, self.run_(wake=True), now=1)
        self.assertIsNotNone(state)
        state, why = kg.after_run(state, self.run_(wake=True), now=2)
        self.assertIsNone(state)
        self.assertIn("no progress", why)

    def test_a_wake_with_progress_resets_the_no_progress_count(self):
        state = kg.arm()
        state, _ = kg.after_run(state, self.run_(wake=True), now=1)
        state, _ = kg.after_run(state, self.run_(wake=True, tools=3), now=2)
        state, why = kg.after_run(state, self.run_(wake=True), now=3)
        self.assertIsNotNone(state)

    def test_the_cap_disarms_and_a_message_from_the_person_resets_the_count(self):
        state = kg.arm(cap=2)
        state, text, why = kg.fire(state)
        self.assertIn("[wake 1/2]", text)
        state, text, why = kg.fire(state)
        self.assertIn("[wake 2/2]", text)
        state, text, why = kg.fire(state)
        self.assertIsNone(state)
        self.assertIsNone(text)
        self.assertIn("2 wakes", why)
        again = kg.on_user_message(kg.fire(kg.arm(cap=2))[0])
        self.assertEqual(0, again["count"])

    def test_the_session_can_arm_it_itself(self):
        state, why = kg.after_run(None, self.run_(text="Long job ahead.\nKEEP GOING"), now=5)
        self.assertIsNotNone(state)
        self.assertIn("armed by the session", why)

    def test_the_wake_names_who_it_may_be_blocked_on(self):
        _, text, _ = kg.fire(kg.arm(), blocked_on="the reviewer")
        self.assertIn("blocked on the reviewer", text)
        _, text, _ = kg.fire(kg.arm())
        self.assertIn("blocked on the person you work for", text)



if __name__ == "__main__":
    unittest.main()
