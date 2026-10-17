"""What the conductor adds to a message on its way to the model: the reminder on a message sent early, the files note, the quote of the message replied to, and the wrapper, whose
text belongs to whoever runs it."""
import unittest

from conductor.delivery import REMINDER, file_note, quote_reply, shown_files, wrap


class DeliveryTest(unittest.TestCase):
    def test_a_reply_quotes_its_target_flattened_and_capped(self):
        out = quote_reply("line one\nline two", "my answer")
        self.assertEqual('(Replying to this earlier message: "line one line two")\n\nmy answer', out)
        long = quote_reply("x" * 400, "ok")
        self.assertIn('"' + "x" * 300 + '…")', long)

    def test_the_wrapper_follows_the_message(self):
        self.assertEqual("hello\n\nSAY WHAT YOU WILL DO", wrap("hello", "SAY WHAT YOU WILL DO"))

    def test_the_files_note_says_where_each_file_is(self):
        note = file_note([{"path": "/s/files/1-cv.pdf", "mime": "application/pdf", "bytes": 2048},
                          {"path": "/s/files/2-a.txt", "mime": "text/plain", "bytes": 12}])
        self.assertIn("- /s/files/1-cv.pdf (application/pdf, 2 KB)", note)
        self.assertIn("- /s/files/2-a.txt (text/plain, 12 B)", note)
        self.assertEqual("", file_note([]))

    def test_the_phone_sees_files_without_paths(self):
        self.assertEqual([{"name": "cv.pdf", "mime": "application/pdf", "bytes": 3}],
                         shown_files([{"name": "cv.pdf", "mime": "application/pdf", "bytes": 3, "path": "/secret"}]))

    def test_the_reminder_says_it_is_not_a_new_request(self):
        self.assertIn("not a new request", REMINDER)


if __name__ == "__main__":
    unittest.main()
