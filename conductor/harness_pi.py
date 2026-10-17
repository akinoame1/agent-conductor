"""pi (`pi --mode rpc`) behind the harness switch.

Nothing new here: the pi knowledge that was spread through the worker — transcript choice, the
followUp prompt, `set_model`, the settle and cost lines — gathered in one place. Its conversation id
is a transcript path, recorded as `pi_session`.
"""
import base64
import json
import os
import time

from .pi_protocol import (
    choose_transcript,
    default_model,
    default_provider,
    enabled_models,
    normalize,
    pi_sessions_dir,
    resume_args,
    settles,
    wire_command,
)
from .harnesses import Fold, Harness


def start_args(pi_session=None, model=None, effort=None):
    """The harness arguments for a start: continue the conversation, and run the model the session
    names. Without `--model` the field was recorded and shown but never applied (found 2026-10-12,
    "How do i change a model?")."""
    args = resume_args(pi_session)
    if model:
        args += ["--model", model]
    if effort:
        args += ["--thinking", effort]
    return args


def _k(n):
    """150000 → "150k"; unknown stays unknown, never 0 (me_and_claude §2)."""
    return "%dk" % (n // 1000) if isinstance(n, int) else "unknown"


class PiHarness(Harness):
    name = "pi"
    label = "pi"
    queues_followups = True
    live_model_switch = True

    def default_command(self):
        return ["pi", "--mode", "rpc"]

    def _root(self, sessions_root):
        return sessions_root or os.path.expanduser("~/.pi/agent/sessions")

    def start(self, rec, history, sessions_root=None):
        # The session's own messages say which transcript is its conversation; the recorded path is
        # the fallback, never the winner when a better match exists (2026-10-11).
        target = choose_transcript(rec.get("pi_session"),
                                   pi_sessions_dir(self._root(sessions_root), rec["cwd"]), history)
        remember = {"pi_session": target} if target and target != rec.get("pi_session") else {}
        argv = self.command + start_args(target, rec.get("model"), rec.get("effort"))
        if rec.get("append_system"):
            # An app assistant's brief and settings list, on top of pi's own prompt.
            argv += ["--append-system-prompt", rec["append_system"]]
        return argv, remember

    def after_start(self, rec, started, sessions_root=None, timeout=6.0):
        """pi writes its transcript a moment after starting; remembering it is what lets a later
        start continue the conversation instead of beginning a new one (2026-10-11)."""
        directory = pi_sessions_dir(self._root(sessions_root), rec["cwd"])
        end = time.time() + timeout
        while time.time() < end:
            try:
                names = [n for n in os.listdir(directory) if n.endswith(".jsonl")]
            except OSError:
                names = []
            fresh = [os.path.join(directory, n) for n in names
                     if os.path.getmtime(os.path.join(directory, n)) >= started - 1]
            if fresh:
                return {"pi_session": max(fresh, key=os.path.getmtime)}
            time.sleep(0.2)
        return {}

    def prompt(self, text, images=None):
        return wire_command("prompt", text, images)

    def steer(self, text, images=None):
        # pi's `steer`: delivered after the current assistant turn's tool calls, before the next
        # LLM call — it may influence the run or not; the queue copy is the guarantee.
        cmd = {"type": "steer", "message": text}
        if images:
            cmd["images"] = [{"type": "image", "data": base64.b64encode(raw).decode(),
                              "mimeType": mime} for mime, _ext, raw in images]
        return cmd

    # pi's `--thinking` levels (docs/rpc-commands.md); xhigh and max only on models that support them.
    efforts = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]

    def set_effort(self, level):
        # pi's RPC `set_thinking_level`: live, the session and its conversation stay.
        return {"type": "set_thinking_level", "level": level}

    def compact(self):
        # pi's RPC `compact` (docs/rpc-commands.md); its reply carries the tokens before and after.
        return {"type": "compact"}

    def abort(self):
        # pi's RPC `abort`: the run stops, the session and its conversation stay.
        return {"type": "abort"}

    def set_model(self, model, provider=None):
        # pi's `set_model` needs provider and model id separately, and it composes them — so a model
        # the dropdown offers as "provider/id" must have that prefix removed, or pi is asked for
        # `openrouter/openrouter/…` and refuses every prompt (found 2026-10-15, the "dev dash"
        # session: each message ran 1 LLM call, 0 tool calls, ~1 s, and answered nothing).
        prov = provider or default_provider()
        if prov and isinstance(model, str) and model.startswith(prov + "/"):
            model = model[len(prov) + 1:]
        return {"type": "set_model", "provider": prov, "modelId": model}

    def fold(self, line, memo):
        out = Fold()
        event = normalize(line)
        if event is not None:
            out.events.append(event)
        try:
            raw = json.loads(line)
        except ValueError:
            return out
        if not isinstance(raw, dict):
            return out
        t = raw.get("type")
        if t == "response" and raw.get("command") == "compact" and raw.get("success"):
            data = raw.get("data") or {}
            before, after = data.get("tokensBefore"), data.get("estimatedTokensAfter")
            out.events.append({"kind": "notice", "text": "compacted: %s → %s tokens (pi's estimate)"
                               % (_k(before), _k(after))})
            if after:
                out.remember["context_tokens"] = after
            return out
        if t == "tool_execution_start":
            out.tool_calls = 1
        elif t == "turn_end":
            out.llm_calls = 1
        elif t == "message_end":
            # The assistant's finished message carries its cost (pi's usage); summed per message it
            # is the run's price.
            usage = (raw.get("message") or {}).get("usage") or {}
            msg = raw.get("message") or {}
            if isinstance(usage, dict) and usage and msg.get("role", "assistant") == "assistant":
                # One model call for the ledger: pi's tokens are exact (they match OpenRouter's
                # native counts); its dollar figure is from its own catalogue, so it is an estimate
                # (docs/usage-ledger.md).
                seen = memo.setdefault("ledger_ids", set())
                call_id = msg.get("responseId")
                if call_id is None or call_id not in seen:
                    if call_id is not None:
                        seen.add(call_id)
                    est = (usage.get("cost") or {}).get("total")
                    out.usage = {
                        "model": msg.get("model"), "provider": msg.get("provider"), "call_id": call_id,
                        "tokens": {"input": usage.get("input") or 0, "cache_read": usage.get("cacheRead") or 0,
                                   "cache_write": usage.get("cacheWrite") or 0, "output": usage.get("output") or 0,
                                   "reasoning": usage.get("reasoning") or 0},
                        "figures": [{"kind": "estimate", "usd": est, "source": "pi catalogue"}]
                                   if isinstance(est, (int, float)) else [],
                        "plan": None,
                    }
            # The level pi actually ran with, stated on every assistant message: what the
            # phone shows when the session asked for none, instead of "default".
            level = (raw.get("message") or {}).get("thinkingLevel")
            if isinstance(level, str) and level:
                out.remember["effort_actual"] = level
            cost = (usage.get("cost") or {}).get("total") if isinstance(usage, dict) else None
            if cost:
                out.cost = float(cost)
            # How full the context is: the run's input plus its cached prefix
            # is what the model actually read. Recorded on the session, so the phone can show it.
            if isinstance(usage, dict):
                ctx = (usage.get("input") or 0) + (usage.get("cacheRead") or 0) + (usage.get("cacheWrite") or 0)
                if ctx:
                    out.remember["context_tokens"] = ctx
        if t in ("agent_start", "turn_start"):
            out.call_started = True
        if settles(t):
            out.settled = True
        elif t == "response" and raw.get("success") is False:
            # pi refused the command before accepting it (no model selected, compaction running).
            out.refused = raw.get("error") or "no reason given"
        return out

    def models(self):
        return enabled_models()

    def default_model(self):
        return default_model()
