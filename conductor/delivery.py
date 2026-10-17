"""What is added to a person's message on its way to the model.

Each piece answers a way models went wrong: a message already sent mid-run and delivered again after it was
taken as insisting; a file attached to a message was invisible to a model that reads text; a reply
without its target lost what it answered. The wrapper's text belongs to whoever runs the conductor — it is
passed in, never fixed here.
"""

# On a queued message that was already sent mid-run and is delivered again after it. A plain duplicate reads as
# insisting: in a probe, Claude Haiku, Sonnet and Opus each redid the work on a plain duplicate, and none did
# with this line.
REMINDER = ("Reminder, not a new request: this message already reached you during your previous run. "
            "If you already acted on it, ignore it and do not repeat the work; otherwise act on it now.")

# How much of a replied-to message is quoted.
QUOTE_MAX = 300


def wrap(text, wrapper):
    """The message, then the wrapper: it sits next to what the model reads, so even a weak model meets the
    demand there and then, not in distant instructions."""
    return text + "\n\n" + wrapper


def quote_reply(target_text, message):
    """The person's words with the message they reply to quoted ahead, flattened to one line and capped."""
    quoted = target_text.strip().replace("\n", " ")
    quoted = quoted[:QUOTE_MAX] + ("…" if len(quoted) > QUOTE_MAX else "")
    return '(Replying to this earlier message: "%s")\n\n%s' % (quoted, message)


def file_note(files):
    """What the model is told about a message's files: where each is, its type and size."""
    if not files:
        return ""
    lines = ["Attached files, on disk (read them with your tools):"]
    for f in files:
        size = f["bytes"]
        lines.append("- %s (%s, %s)" % (f["path"], f.get("mime") or "?",
                                        "%d KB" % round(size / 1024) if size >= 1024 else "%d B" % size))
    return "\n\n" + "\n".join(lines)


def shown_files(files):
    """The files as a client shows them: name, type and size, never the path on the server."""
    return [{k: f[k] for k in ("name", "mime", "bytes")} for f in files]
