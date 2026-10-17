"""pi's side of the conversation: its settings, its JSON-lines events normalised for a screen, which of
them settle a run, the commands it takes, and finding a session's transcript to resume. Moved verbatim from
infra_server/devsessions.py, where none of it touched the session store."""
import base64
import json
import os
import re
import time
from datetime import datetime, timezone

# pi's RPC stream, in the shapes a screen draws. Everything else is the same event half-built:
# `message_update` arrives per token and `tool_execution_update` per chunk, and storing each one
# filled the log with 10,112 empty cards for one short session (2026-10-11).
_NOISE = {"message_update", "tool_execution_update", "message_start", "turn_start", "agent_start",
          "agent_settled", "response", "turn_end"}

def enabled_models(settings_path=None):
    """The models pi is set up to run — its settings' `enabledModels` — so the phone offers a
    dropdown instead of free text."""
    path = settings_path or os.environ.get("PI_SETTINGS") or os.path.expanduser("~/.pi/agent/settings.json")
    try:
        with open(path) as fh:
            return json.load(fh).get("enabledModels") or []
    except (OSError, ValueError):
        return []

def default_provider(settings_path=None):
    """The provider pi will run the model through when none is named — its `defaultProvider`
    (`openrouter` here). `set_model` needs provider and model id separately (pi's RPC)."""
    path = settings_path or os.environ.get("PI_SETTINGS") or os.path.expanduser("~/.pi/agent/settings.json")
    try:
        with open(path) as fh:
            return json.load(fh).get("defaultProvider")
    except (OSError, ValueError):
        return None

def default_model(settings_path=None):
    """The model pi will actually run when a session names none: its settings' `defaultModel`.

    The session
    said `model not set` because the phone names no model and the server recorded none, while pi
    used its own default. Record that, so the state line shows the real model. `PI_SETTINGS` lets a
    test point at a temp file.
    """
    path = settings_path or os.environ.get("PI_SETTINGS") or os.path.expanduser("~/.pi/agent/settings.json")
    try:
        with open(path) as fh:
            return json.load(fh).get("defaultModel")
    except (OSError, ValueError):
        return None

def _text_of(message):
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(p for p in parts if p) or None
    return None

def _one_line(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)

def _tool_call(msg):
    name = msg.get("toolName") or msg.get("name") or "tool"
    args = msg.get("args", msg.get("arguments"))
    return {"kind": "tool_call", "text": name + (" " + _one_line(args) if args else ""), "raw": msg}

def _tool_result(msg):
    result = msg.get("result")
    text = _text_of(result) if isinstance(result, dict) else (result if isinstance(result, str) else None)
    name = msg.get("toolName") or "tool"
    return {"kind": "tool_result", "text": (name + "\n" + text) if text else name, "raw": msg}

def pi_sessions_dir(root, cwd):
    """Where pi keeps the transcripts for one working directory: `--home-me-work--`."""
    return os.path.join(root, "-" + cwd.replace("/", "-") + "--")

def resume_args(pi_session):
    """The arguments that continue an existing conversation, if there is one to continue."""
    return ["--session", pi_session] if pi_session else []

def _user_texts(path):
    """Every user message a transcript holds, so a session can be matched to its own file."""
    found = set()
    try:
        with open(path, errors="ignore") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                message = entry.get("message") or {}
                if message.get("role") != "user":
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    found.add(content.strip())
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("text"):
                            found.add(part["text"].strip())
    except OSError:
        pass
    return found

def best_transcript(directory, wanted):
    """The transcript for a session with no recorded one: the file holding the most of its own
    messages. The newest is not enough — a blank session started by mistake is usually the newest.
    """
    wanted = {w.strip() for w in wanted if w and w.strip()}
    if not wanted:
        return None
    try:
        names = [n for n in os.listdir(directory) if n.endswith(".jsonl")]
    except OSError:
        return None
    best, best_score = None, 0
    for name in names:
        path = os.path.join(directory, name)
        score = len(_user_texts(path) & wanted)
        if score > best_score:
            best, best_score = path, score
    return best

def choose_transcript(recorded, directory, wanted):
    """Which transcript a start should resume: the one holding this session's own messages, and
    only failing that whatever was recorded. A recorded blank must not win over the real
    conversation still on disk (2026-10-11)."""
    return best_transcript(directory, wanted) or recorded

def quiet_seconds(last, now):
    """How long a session has said nothing — the number that tells "working" from "stuck"."""
    if not last:
        return None
    try:
        at = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return max(0.0, round(now - at, 1))

def settles(raw_type):
    """Whether a harness line means the run is over.

    `turn_end` does NOT: pi emits it after every assistant response inside one run (assistant →
    tools → assistant), so treating it as the end made a working session read `idle` and wrote a
    "turn ended" card each time — 190 of them in one session. Only
    `agent_settled` (pi will not continue) and `agent_end` end the run.
    """
    return raw_type in ("agent_end", "agent_settled")

def wire_command(kind, message, images=None):
    """The one command the harness is sent — **always a follow-up**.

    A message sent while the agent is working is an input for later: a follow-up is delivered only
    when the run finishes, as its own turn. A `steer` is delivered mid-run, before the next LLM
    call, where it is absorbed into the work in progress and lost.

    A `prompt` carrying a `streamingBehavior` is right both ways: while streaming it queues, and
    while idle the behavior is ignored and a run starts — so a message never dies on a guess about
    the harness's state. `kind` is kept for the call site's meaning, not the wire."""
    cmd = {"type": "prompt", "message": message, "streamingBehavior": "followUp"}
    if images:
        # pi's RPC takes images with the prompt: [(mime, ext, bytes)] from the phone.
        cmd["images"] = [{"type": "image", "data": base64.b64encode(raw).decode(), "mimeType": mime}
                         for mime, _ext, raw in images]
    return cmd

def normalize(line):
    """One harness output line → one event the screen draws, or `None` for streaming/lifecycle
    noise. Only the settled shapes become events; an unknown shape is still kept, never dropped."""
    try:
        msg = json.loads(line)
    except ValueError:
        return {"kind": "event", "text": line.rstrip("\n"), "raw": {"line": line.rstrip("\n")}}
    if not isinstance(msg, dict):
        return {"kind": "event", "text": str(msg), "raw": msg}
    t = msg.get("type")
    if t in _NOISE:
        return None
    if t == "message_end":
        message = msg.get("message") or {}
        # Only the assistant's own words are an answer. pi echoes the user's message back (role
        # user) and emits each tool result again (role toolResult); both are already events — the
        # `sent` line and the `tool_result` — and drawing them made the transcript read twice and
        # put the technical work among the answers.
        if message.get("role") != "assistant":
            return None
        text = _text_of(message)
        if not text:
            return None      # a message that was only a tool call; the call is its own event
        return {"kind": "text", "text": text, "role": "assistant", "raw": msg}
    if t in ("tool_call", "tool_execution_start"):
        return _tool_call(msg)
    if t in ("tool_result", "tool_execution_end"):
        return _tool_result(msg)
    if t in ("status", "cost"):
        return {"kind": t, "text": msg.get("text") or "", "raw": msg}
    if t == "agent_end":
        return {"kind": "status", "text": "turn ended", "raw": msg}
    return {"kind": "event", "text": "", "raw": msg}
