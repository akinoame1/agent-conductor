"""Claude Code behind the harness switch.

`claude -p --input-format stream-json --output-format stream-json --verbose` is pi's RPC mode's
twin, measured 2026-10-14 against Claude Code 2.1.299: one long-lived process, a JSON line in per
message, a JSON line out per event, and a `result` line when a run is over (with its cost). The
differences this module absorbs:

- **Follow-ups.** A message written while a run is going is merged into that run (a steer): one
  reply, one `result` (measured 2026-10-16). the person wants every message delivered as its own turn, so
  `queues_followups` is False and the worker holds it until `result` — except the queue's Send,
  which is the person asking for that merge (`steer`).
- **The conversation id is ours.** The first start names it (`--session-id`), later ones resume it
  (`--resume`); no transcript has to be guessed after the fact, as pi's must.
- **Permissions.** `--permission-mode auto`: the same mode the developer's own sessions run in, and
  it works with no one at a prompt.
- **Limits, not funds.** It runs on the Claude plan, and `rate_limit_event` says how much of each
  window is used; that is recorded as the session's `limits`.
"""
import base64
import json
import os
import re
import uuid

from .harnesses import Fold, Harness

# The models offered at start, newest family first. Full ids, not aliases, so the phone shows what
# actually runs; `DEV_CLAUDE_MODELS` (comma-separated) replaces the list without a deploy.
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]


def projects_dir(root, cwd):
    """Where Claude Code keeps one working directory's transcripts: every character that is not a
    letter or digit becomes `-` (`/home/me/work` → `-home-me-work`)."""
    return os.path.join(root, re.sub(r"[^A-Za-z0-9]", "-", cwd))


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text" and c.get("text"))
    return ""


class ClaudeHarness(Harness):
    name = "claude"
    label = "Claude Code"
    queues_followups = False
    live_model_switch = True

    def default_command(self):
        return ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--permission-mode", "auto"]

    def _root(self, sessions_root):
        return sessions_root or os.path.expanduser("~/.claude/projects")

    def start(self, rec, history, sessions_root=None):
        conversation = rec.get("claude_session")
        remember = {}
        if not conversation:
            conversation = str(uuid.uuid4())
            remember["claude_session"] = conversation
        transcript = os.path.join(projects_dir(self._root(sessions_root), rec["cwd"]),
                                  conversation + ".jsonl")
        # `--session-id` refuses an id that exists and `--resume` one that does not, so the file
        # decides: a session stopped before its first message has no transcript yet.
        if os.path.exists(transcript):
            argv = self.command + ["--resume", conversation]
        else:
            argv = self.command + ["--session-id", conversation]
        if rec.get("model"):
            argv += ["--model", rec["model"]]
        if rec.get("effort"):
            argv += ["--effort", rec["effort"]]
        if rec.get("append_system"):
            # An app assistant's brief and settings list, on top of Claude Code's own prompt.
            argv += ["--append-system-prompt", rec["append_system"]]
        return argv, remember

    def prompt(self, text, images=None):
        content = [{"type": "image", "source": {"type": "base64", "media_type": mime,
                                                "data": base64.b64encode(raw).decode()}}
                   for mime, _ext, raw in images or []]
        content.append({"type": "text", "text": text})
        return {"type": "user", "message": {"role": "user", "content": content}}

    # Claude Code's `--effort` levels. Its stream-json input refuses `set_effort`; the change is a
    # restart on the same conversation (set_effort stays None), probed 2026-10-16.
    efforts = ["low", "medium", "high", "xhigh", "max"]

    def compact(self):
        # Claude Code's own /compact, sent as a message in stream-json (probed 2026-10-16).
        return self.prompt("/compact")

    def steer(self, text, images=None):
        # Claude Code folds a mid-run message into the running turn, so the steer is the message
        # itself. Only the queue's Send uses it; until this was missing, and Send did nothing.
        return self.prompt(text, images)

    def set_model(self, model, provider=None):
        return {"type": "control_request", "request_id": "model-" + uuid.uuid4().hex[:8],
                "request": {"subtype": "set_model", "model": model}}

    def fold(self, line, memo):
        out = Fold()
        try:
            raw = json.loads(line)
        except ValueError:
            out.events.append({"kind": "event", "text": line.rstrip("\n"), "raw": {"line": line}})
            return out
        if not isinstance(raw, dict):
            return out
        t = raw.get("type")
        if t == "system" and raw.get("subtype") == "compact_boundary":
            # /compact finished: Claude Code's own before/after.
            meta = raw.get("compact_metadata") or {}
            pre, post = meta.get("pre_tokens"), meta.get("post_tokens")
            k = lambda n: "%dk" % (n // 1000) if isinstance(n, int) else "unknown"
            out.events.append({"kind": "notice", "text": "compacted: %s → %s tokens" % (k(pre), k(post))})
            if post:
                out.remember["context_tokens"] = post
            return out
        if t == "system":
            if raw.get("subtype") == "init":
                out.remember["claude_session"] = raw.get("session_id")
            else:
                # Thinking, task starts: the model is at work on what was delivered.
                out.call_started = True
            return out
        if t == "assistant":
            # How full the context is, as pi's sessions record it: what
            # the model read on this call is its input plus the cached prefix, read or written.
            msg = raw.get("message") or {}
            usage = msg.get("usage") or {}
            if isinstance(usage, dict):
                ctx = sum(usage.get(k) or 0 for k in
                          ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                if ctx:
                    out.remember["context_tokens"] = ctx
            if msg.get("model"):
                memo["model"] = msg["model"]
            # One model call per message id, counted as it happens: the result's num_turns
            # arrived only at the end, so the run line said "0 LLM calls" mid-run. Claude Code repeats
            # a message's line per content block, so the id, not the line, is the call.
            seen = memo.setdefault("message_ids", set())
            if msg.get("id") and msg["id"] not in seen:
                seen.add(msg["id"])
                out.llm_calls = 1
                # One model call for the ledger: exact tokens and the plan windows as last
                # reported. No dollar figure: Claude Code's is list price, per run, not a bill.
                out.usage = {
                    "model": msg.get("model"), "provider": "anthropic", "call_id": msg["id"],
                    "tokens": {"input": usage.get("input_tokens") or 0,
                               "cache_read": usage.get("cache_read_input_tokens") or 0,
                               "cache_write": usage.get("cache_creation_input_tokens") or 0,
                               "output": usage.get("output_tokens") or 0, "reasoning": 0},
                    "figures": [], "plan": memo.get("plan"),
                }
            out.call_started = True
            message = raw.get("message") or {}
            text = []
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    text.append(block["text"])
                elif block.get("type") == "tool_use":
                    name = block.get("name") or "tool"
                    memo.setdefault("tools", {})[block.get("id")] = name
                    out.tool_calls += 1
                    out.events.append({"kind": "tool_call", "raw": block,
                                       "text": name + " " + json.dumps(block.get("input"),
                                                                       ensure_ascii=False)})
            if text:
                out.events.append({"kind": "text", "role": "assistant", "text": "\n".join(text),
                                   "raw": raw})
            return out
        if t == "user":
            # Tool results come back as the user's turn; the person's own words are already the
            # `sent` line, and a command's echo (`/model`) is not an answer.
            content = (raw.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name = memo.get("tools", {}).pop(block.get("tool_use_id"), "tool")
                    body = _text(block.get("content"))
                    out.events.append({"kind": "tool_result", "raw": block,
                                       "text": name + ("\n" + body if body else "")})
            return out
        if t == "result":
            # The window, as Claude Code states it for the model that answered (a subagent's model
            # may also be listed); never a table of ours.
            usage = raw.get("modelUsage") or {}
            entry = usage.get(memo.get("model")) if isinstance(usage, dict) else None
            if entry is None and isinstance(usage, dict) and len(usage) == 1:
                entry = next(iter(usage.values()))
            if isinstance(entry, dict) and entry.get("contextWindow"):
                out.remember["context_window"] = entry["contextWindow"]
            out.settled = True
            out.llm_calls_total = raw.get("num_turns")
            if raw.get("total_cost_usd"):
                # Per run, not per session: measured 2026-10-14 (three identical runs, each reading
                # its whole context from the cache).
                out.cost = float(raw["total_cost_usd"])
            if raw.get("is_error") or raw.get("subtype") not in (None, "success"):
                why = raw.get("result") if isinstance(raw.get("result"), str) else None
                # A failed run is a refusal as far as the queue is concerned: the
                # harness did not take the message, so the queue must not flush into it. The
                # worker's refused branch says the notice; saying it twice reads as two failures.
                out.refused = why or ", ".join(raw.get("errors") or []) or raw.get("subtype")
            return out
        if t == "rate_limit_event":
            out.remember["limits"] = raw.get("rate_limit_info")
            windows = (raw.get("rate_limit_info") or {}).get("unifiedWindows") or {}
            memo["plan"] = {k: (windows.get(k) or {}).get("utilization") for k in ("five_hour", "seven_day")}
            return out
        if t == "control_response":
            response = raw.get("response") or {}
            if response.get("subtype") == "error":
                out.events.append({"kind": "error", "raw": raw,
                                   "text": "the harness refused that: %s" % response.get("error")})
            return out
        if t == "stream_event":
            return out
        out.events.append({"kind": "event", "text": "", "raw": raw})
        return out

    def models(self):
        listed = os.environ.get("DEV_CLAUDE_MODELS")
        return [m.strip() for m in listed.split(",") if m.strip()] if listed else list(MODELS)

    def default_model(self):
        try:
            with open(os.path.expanduser("~/.claude/settings.json")) as fh:
                chosen = json.load(fh).get("model")
        except (OSError, ValueError):
            chosen = None
        return chosen or self.models()[0]
