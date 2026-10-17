"""The harness adapters: what a line of pi's or Claude Code's output means to the conductor."""
import json
import unittest

from conductor.harnesses import registry


def fold(harness, lines):
    memo = {}
    return [harness.fold(json.dumps(line), memo) for line in lines]


class PiTest(unittest.TestCase):
    def test_answer_tool_and_settle(self):
        folds = fold(registry()["pi"], [
            {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
                                                "content": [{"type": "text", "text": "hello"}]}},
            {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}},
            {"type": "agent_end"},
        ])
        self.assertEqual(["text"], [e["kind"] for e in folds[0].events])
        self.assertEqual("hello", folds[0].events[0]["text"])
        self.assertEqual(1, folds[1].tool_calls)
        self.assertTrue(folds[2].settled)

    def test_the_echo_of_the_users_message_is_not_an_answer(self):
        folds = fold(registry()["pi"], [{"type": "message_end", "message": {"role": "user",
                                                                           "content": [{"type": "text", "text": "hi"}]}}])
        self.assertEqual([], folds[0].events)


class ClaudeTest(unittest.TestCase):
    def test_calls_tools_and_the_result_settles_with_its_own_count(self):
        folds = fold(registry()["claude"], [
            {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "hi"}]}},
            {"type": "assistant", "message": {"id": "m2", "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "result", "subtype": "success", "num_turns": 2, "is_error": False, "result": "hi"},
        ])
        self.assertEqual((1, 0), (folds[0].llm_calls, folds[0].tool_calls))
        self.assertEqual(1, folds[1].tool_calls)
        self.assertTrue(folds[2].settled)
        self.assertEqual(2, folds[2].llm_calls_total)


if __name__ == "__main__":
    unittest.main()
