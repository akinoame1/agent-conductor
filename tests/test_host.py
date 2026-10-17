"""The reference host: a session in a folder, the harness as a subprocess, the engine between them. Driven by a
fake pi (tests/fake_pi.py), so it runs anywhere."""
import os
import sys
import tempfile
import time
import unittest

from conductor.harnesses import registry
from conductor.host import Session

FAKE = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_pi.py")]


class HostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        harness = registry({"pi": FAKE})["pi"]
        self.s = Session(os.path.join(self.tmp.name, "s1"), harness, cwd=self.tmp.name, wrapper="SAY WHAT YOU WILL DO")
        self.s.start()

    def tearDown(self):
        self.s.stop()
        self.tmp.cleanup()

    def wait(self, cond, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            self.s.pump()
            if cond():
                return True
            time.sleep(0.05)
        self.fail("never became true")

    def texts(self):
        return [e["text"] for e in self.s.events() if e["kind"] == "text"]

    def test_a_message_is_answered_and_the_session_settles(self):
        self.s.submit("hello")
        self.wait(lambda: "heard: hello" in self.texts() and self.s.state() == "idle")
        self.assertEqual("sent", self.s.events()[0]["kind"])

    def test_a_message_written_while_it_works_is_held_wrapped_then_delivered(self):
        self.s.submit("SLOW first")
        self.assertEqual("queued", self.s.submit("second"))
        self.assertEqual(["second"], [q["text"] for q in self.s.queue()])
        self.wait(lambda: any(t.startswith("heard: second") for t in self.texts()))
        self.assertIn("SAY WHAT YOU WILL DO", [t for t in self.texts() if t.startswith("heard: second")][0])
        self.assertEqual([], self.s.queue())

    def test_stop_after_holds_the_rest_until_continue(self):
        self.s.submit("SLOW first")
        self.s.submit("A")
        self.s.submit("B")
        a = [q for q in self.s.queue() if q["text"] == "A"][0]
        self.s.stop_after(a["qid"], True)
        self.wait(lambda: any(t.startswith("heard: A") for t in self.texts()) and self.s.state() == "idle")
        time.sleep(0.5); self.s.pump()
        self.assertEqual(["B"], [q["text"] for q in self.s.queue()])
        self.s.resume()
        self.wait(lambda: any(t.startswith("heard: B") for t in self.texts()))

    def test_it_survives_a_restart_of_the_host(self):
        self.s.submit("hello")
        self.wait(lambda: self.s.state() == "idle")
        again = Session(self.s.root, self.s.harness, cwd=self.tmp.name, wrapper="W")
        self.assertIn("heard: hello", [e["text"] for e in again.events() if e["kind"] == "text"])


if __name__ == "__main__":
    unittest.main()
