"""The queue: what the person writes while the agent works is held -- visible, editable, reorderable -- and
delivered one message at a time after each run. A message sent early is marked and delivered later with a reminder; a marked message stops the queue after it until the person continues.

Storage is an interface (load and save the list), so the engine is the same over a file, memory or a database.
Callers hold their own lock around a read-modify-write: the engine does not assume one process.
"""
import json


class MemoryQueueStore:
    def __init__(self):
        self._items = []

    def load(self):
        return [dict(i) for i in self._items]

    def save(self, items):
        self._items = [dict(i) for i in items]


class FileQueueStore:
    """One JSON object per line. A damaged file reads as empty rather than stopping the session."""

    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path) as fh:
                return [json.loads(l) for l in fh if l.strip()]
        except (OSError, ValueError):
            return []

    def save(self, items):
        with open(self.path, "w") as fh:
            fh.writelines(json.dumps(i) + "\n" for i in items)

    def append(self, item):
        with open(self.path, "a") as fh:
            fh.write(json.dumps(item) + "\n")


class Queue:
    def __init__(self, store):
        self.store = store

    def items(self):
        return self.store.load()

    def view(self):
        """What a client shows: no wire payload."""
        return [{"qid": q.get("qid"), "text": q.get("text", ""), "images": q.get("images"),
                 "seq": q.get("seq"), "in_reply_to": q.get("in_reply_to"),
                 "steered": bool(q.get("steered")), "stop_after": bool(q.get("stop_after"))}
                for q in self.items()]

    def hold(self, qid, seq, payload, text="", images=None, in_reply_to=None, delivered_as=None, files=None):
        item = {"qid": qid, "seq": seq, "text": text, "images": images, "payload": payload}
        if files:
            item["files"] = files                 #: the files note is rebuilt from these on edit
        if in_reply_to is not None:
            item["in_reply_to"] = in_reply_to     # the message it answers
        if delivered_as is not None:
            item["delivered_as"] = delivered_as   # the exact text the model will get
        if hasattr(self.store, "append"):
            self.store.append(item)
        else:
            self.store.save(self.items() + [item])
        return item

    def _update(self, qid, change):
        items = self.items()
        for q in items:
            if q.get("qid") == qid:
                change(q)
                self.store.save(items)
                return True
        return False

    def set_stop_after(self, qid, on):
        return self._update(qid, lambda q: q.__setitem__("stop_after", bool(on)))

    def mark_steered(self, qid, payload, delivered_as):
        def change(q):
            q["steered"], q["payload"], q["delivered_as"] = True, payload, delivered_as
        return self._update(qid, change)

    def edit(self, qid, text, payload, images, delivered_as=None):
        def change(q):
            q["text"], q["payload"], q["images"] = text, payload, images
            if delivered_as is not None:
                q["delivered_as"] = delivered_as
        return self._update(qid, change)

    def discard(self, qid):
        items = self.items()
        kept = [q for q in items if q.get("qid") != qid]
        if len(kept) == len(items):
            return False
        self.store.save(kept)
        return True

    def move(self, qid, index):
        items = self.items()
        item = next((q for q in items if q.get("qid") == qid), None)
        if item is None:
            return False
        items.remove(item)
        items.insert(max(0, min(int(index), len(items))), item)
        self.store.save(items)
        return True

    def next(self, paused):
        """The message to deliver now, or None (nothing held, or stopped after a marked one). Not removed:
        a failed send must not lose it."""
        if paused:
            return None
        items = self.items()
        return items[0] if items else None

    def delivered(self, qid):
        """It went to the harness: drop it. True when the queue should now pause -- it was marked stop-after
        and more wait behind it."""
        items = self.items()
        item = next((q for q in items if q.get("qid") == qid), None)
        if item is None:
            return False
        rest = [q for q in items if q is not item]
        self.store.save(rest)
        return bool(item.get("stop_after")) and bool(rest)
