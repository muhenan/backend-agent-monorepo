"""Optional live streaming check; cleans only its own temporary memory namespace."""

import json
import time
import urllib.request
from uuid import uuid4

BASE_URL = "http://localhost:8000"


def stream_turn(user_id, message):
    started = time.monotonic()
    request = urllib.request.Request(
        f"{BASE_URL}/api/chat/stream",
        data=json.dumps({"user_id": user_id, "message": message}).encode(),
        headers={"Content-Type": "application/json"},
    )
    events = []
    with urllib.request.urlopen(request, timeout=180) as response:
        assert "text/event-stream" in response.headers["Content-Type"]
        for line in response:
            if line.startswith(b"data: "):
                event = json.loads(line[6:])
                event["received_at"] = round(time.monotonic() - started, 3)
                events.append(event)
                if event["type"] == "error":
                    raise AssertionError(event["message"])
    kinds = [event["type"] for event in events]
    assert kinds[0] == "status"
    assert "text_delta" in kinds
    assert kinds.index("text_delta") < kinds.index("answer_done")
    assert kinds.index("answer_done") < kinds.index("memory_write_start")
    assert kinds.index("memory_write_start") < kinds.index("memory_write_done")
    assert kinds[-1] == "done"
    done = events[-1]
    print(
        json.dumps(
            {
                "deltas": kinds.count("text_delta"),
                "first_text_seconds": next(
                    e["received_at"] for e in events if e["type"] == "text_delta"
                ),
                "answer_seconds": next(
                    e["received_at"] for e in events if e["type"] == "answer_done"
                ),
                "complete_seconds": done["received_at"],
                "tool_calls": kinds.count("tool_start"),
                "write_status": done["memory_write"]["status"],
            }
        ),
        flush=True,
    )
    return events


def main():
    user_id = f"stream-smoke-{uuid4().hex}"
    try:
        general = stream_turn(user_id, "请用三句话解释 Python 的列表与元组。")
        assert not general[-1]["memory_searches"]
        saved = stream_turn(user_id, "请记住，我叫测试小舟，最喜欢的饮料是桂花乌龙茶。")
        assert saved[-1]["memory_write"]["status"] == "saved"
        recalled = stream_turn(user_id, "请查询我们以前聊过的记忆：我叫什么，喜欢喝什么？")
        assert recalled[-1]["memory_searches"]
        assert "小舟" in recalled[-1]["answer"]
        assert "乌龙" in recalled[-1]["answer"]
        starts = {event["id"] for event in recalled if event["type"] == "tool_start"}
        ends = {event["id"] for event in recalled if event["type"] == "tool_end"}
        assert starts == ends
        print("LIVE STREAM SMOKE PASS", flush=True)
    finally:
        request = urllib.request.Request(
            f"{BASE_URL}/api/memories?user_id={user_id}", method="DELETE"
        )
        with urllib.request.urlopen(request, timeout=30):
            pass


if __name__ == "__main__":
    main()
