"""The queue as the conductor's engine. Messages written while the agent
works are held — visible, editable, reorderable — and delivered one at a time after each run, unless a marked
message stops it. The same contract runs against the memory store and the file store."""
import os
import tempfile
import unittest

from conductor.queue import FileQueueStore, MemoryQueueStore, Queue


class QueueContract:
    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        self.q = Queue(self.make_store())
        for qid in ("a", "b", "c"):
            self.q.hold(qid=qid, seq=ord(qid), payload={"message": qid.upper()}, text=qid.upper())

    def test_held_in_order_and_shown_without_the_payload(self):
        self.assertEqual(["a", "b", "c"], [i["qid"] for i in self.q.items()])
        view = self.q.view()
        self.assertEqual(["A", "B", "C"], [v["text"] for v in view])
        self.assertNotIn("payload", view[0])
        self.assertEqual((False, False), (view[0]["steered"], view[0]["stop_after"]))

    def test_move_discard_and_edit(self):
        self.assertTrue(self.q.move("c", 0))
        self.assertEqual(["c", "a", "b"], [i["qid"] for i in self.q.items()])
        self.assertTrue(self.q.discard("a"))
        self.assertFalse(self.q.discard("a"))
        self.assertTrue(self.q.edit("b", "B2", {"message": "B2"}, None, delivered_as="B2 wrapped"))
        b = [i for i in self.q.items() if i["qid"] == "b"][0]
        self.assertEqual(("B2", "B2 wrapped"), (b["text"], b["delivered_as"]))
        self.assertFalse(self.q.move("zz", 1))

    def test_a_steered_message_stays_queued_marked(self):
        self.assertTrue(self.q.mark_steered("b", {"message": "REMINDER B"}, "REMINDER B"))
        self.assertTrue([v for v in self.q.view() if v["qid"] == "b"][0]["steered"])
        self.assertEqual(3, len(self.q.items()))

    def test_next_looks_without_removing_and_delivered_removes(self):
        # A failed send must not lose the message: looking and removing are two steps.
        self.assertEqual("a", self.q.next(paused=False)["qid"])
        self.assertEqual(3, len(self.q.items()))
        self.assertFalse(self.q.delivered("a"))
        self.assertEqual(["b", "c"], [i["qid"] for i in self.q.items()])

    def test_a_stop_after_mark_pauses_once_more_wait(self):
        self.q.set_stop_after("a", True)
        self.assertTrue(self.q.delivered("a"))                 # pause: more wait behind it
        self.assertIsNone(self.q.next(paused=True))
        self.assertEqual("b", self.q.next(paused=False)["qid"])

    def test_the_last_marked_message_does_not_pause_an_empty_queue(self):
        self.q.set_stop_after("c", True)
        self.q.delivered("a"); self.q.delivered("b")
        self.assertFalse(self.q.delivered("c"))
        self.assertIsNone(self.q.next(paused=False))


class MemoryQueueTest(QueueContract, unittest.TestCase):
    def make_store(self):
        return MemoryQueueStore()


class FileQueueTest(QueueContract, unittest.TestCase):
    def make_store(self):
        self.tmp = tempfile.TemporaryDirectory()
        return FileQueueStore(os.path.join(self.tmp.name, "followups.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_damaged_file_reads_as_empty_not_a_crash(self):
        path = os.path.join(self.tmp.name, "bad.jsonl")
        open(path, "w").write("{not json\n")
        self.assertEqual([], Queue(FileQueueStore(path)).items())


if __name__ == "__main__":
    unittest.main()
