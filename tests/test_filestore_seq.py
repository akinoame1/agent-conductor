"""Two clients on one session folder never share an event number. The phone host behind this library numbered
events with a counter per process, and two events of one log both got seq 84; the phone then crashed opening it
(akinoame1/app_infra#470). FileStore kept the same counter (app_infra#481). A seq is the event's line number."""
import json
import multiprocessing
import os
import tempfile
import unittest

from conductor.host import FileStore


def _append_many(root, n, tag):
    store = FileStore(root)
    for i in range(n):
        store.append(None, {"kind": "text", "text": "%s %d" % (tag, i)})


class SeqTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def seqs(self):
        with open(os.path.join(self.root, "events.jsonl")) as fh:
            return [json.loads(l)["seq"] for l in fh if l.strip()]

    def test_two_stores_taking_turns_never_reuse_a_number(self):
        a, b = FileStore(self.root), FileStore(self.root)
        a.append(None, {"kind": "text", "text": "1"})
        b.append(None, {"kind": "sent", "text": "2"})
        a.append(None, {"kind": "text", "text": "3"})
        self.assertEqual([1, 2, 3], self.seqs())
        self.assertEqual(["3"], [e["text"] for e in b.events_since(None, 2)])

    @unittest.skipUnless(hasattr(os, "fork"), "needs fork")
    def test_processes_appending_at_once_get_each_line_its_own_number(self):
        ctx = multiprocessing.get_context("fork")
        procs = [ctx.Process(target=_append_many, args=(self.root, 50, "p%d" % k)) for k in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertEqual(list(range(1, 201)), self.seqs())

    def test_a_log_that_already_drifted_numbers_new_events_by_their_line(self):
        with open(os.path.join(self.root, "events.jsonl"), "w") as fh:
            for seq in (1, 2, 2):
                fh.write(json.dumps({"kind": "text", "seq": seq}) + "\n")
        self.assertEqual(4, FileStore(self.root).append(None, {"kind": "text", "text": "new"})["seq"])


if __name__ == "__main__":
    unittest.main()
