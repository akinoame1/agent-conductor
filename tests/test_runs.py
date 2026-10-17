"""The run tracker. From each folded harness line: the run's LLM and tool
calls, its last answer (keep-going reads it), and at the settle whether the session goes idle or straight on
to a message the harness itself held. Pricing, tokens and plan figures are not the conductor's: a usage hook adds
them."""
import unittest

from conductor.harnesses import Fold
from conductor.runs import after_settle, apply, new_run, worth_a_summary


class RunsTest(unittest.TestCase):
    def test_counts_and_the_last_answer(self):
        run = new_run(now=100.0)
        apply(run, Fold(tool_calls=2, llm_calls=1))
        apply(run, Fold(llm_calls=1, events=[{"kind": "text", "role": "assistant", "text": "first"}]))
        apply(run, Fold(events=[{"kind": "text", "role": "assistant", "text": "Done. NO MORE WORK: shipped"}]))
        self.assertEqual((2, 2), (run["llm_calls"], run["tool_calls"]))
        self.assertEqual("Done. NO MORE WORK: shipped", run["text"])

    def test_a_harness_that_counts_its_own_calls_sets_the_total(self):
        run = new_run(now=0)
        apply(run, Fold(llm_calls=1))
        apply(run, Fold(llm_calls_total=7))
        self.assertEqual(7, run["llm_calls"])

    def test_the_usage_hook_adds_its_own_fields(self):
        seen = []
        def usage(run, fold):
            seen.append(fold.usage)
            run["tokens"] = run.get("tokens", 0) + fold.usage["tokens"]
        run = new_run(now=0)
        self.assertTrue(apply(run, Fold(usage={"tokens": 10}), usage=usage))
        apply(run, Fold(usage={"tokens": 5}), usage=usage)
        self.assertEqual(15, run["tokens"])
        self.assertEqual(2, len(seen))

    def test_apply_says_whether_anything_changed(self):
        run = new_run(now=0)
        self.assertFalse(apply(run, Fold()))
        self.assertTrue(apply(run, Fold(tool_calls=1)))

    def test_a_run_that_did_nothing_gets_no_summary(self):
        self.assertFalse(worth_a_summary(new_run(now=0)))
        run = new_run(now=0); apply(run, Fold(tool_calls=1))
        self.assertTrue(worth_a_summary(run))

    def test_settling_goes_idle_or_on_to_the_held_message(self):
        self.assertEqual(("idle", None), after_settle(new_run(now=0), now=50))
        state, nxt = after_settle(new_run(now=0, queued=2), now=50)
        self.assertEqual("next", state)
        self.assertEqual((50, 1, 0, 0), (nxt["start"], nxt["queued"], nxt["llm_calls"], nxt["tool_calls"]))


if __name__ == "__main__":
    unittest.main()
