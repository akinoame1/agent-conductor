"""A stand-in for `pi --mode rpc`: each prompt on stdin is answered "heard: <text>", then the run ends.
"SLOW" in a prompt waits 1.5 s first, so a test can send while it works; "TOOL" adds one tool call."""
import json
import sys
import time


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    msg = json.loads(raw)
    if msg.get("type") not in ("prompt", "follow_up", "steer"):
        continue
    text = msg.get("message", "")
    out({"type": "agent_start"})
    if "SLOW" in text:
        time.sleep(1.5)
    if "TOOL" in text:
        out({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}})
    out({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
                                            "content": [{"type": "text", "text": "heard: " + text}]}})
    out({"type": "agent_end"})
