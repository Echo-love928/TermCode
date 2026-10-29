"""本地 HTTP 服务直接向官方 SDK 提供 SSE，不访问外部模型。"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class SSEServer:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.mode = "normal"
        self.delay = 0.005
        self.responses: list[dict] = []
        self.gate = threading.Event()
        self.gate.set()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(
                    {
                        "path": self.path,
                        "body": body,
                        "headers": {
                            key.lower(): value for key, value in self.headers.items()
                        },
                    }
                )
                if owner.mode == "auth_error":
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {
                                "error": {
                                    "type": "authentication_error",
                                    "message": "TEST_SECRET",
                                }
                            }
                        ).encode()
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                owner.gate.wait(timeout=5)
                try:
                    if owner.responses:
                        self.scripted(owner.responses.pop(0))
                    elif self.path.endswith("/messages"):
                        self.anthropic(body)
                    else:
                        self.openai()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def event(self, body: dict, event: str | None = None) -> None:
                prefix = f"event: {event}\n" if event else ""
                data = prefix + "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
                time.sleep(owner.delay)

            def scripted(self, response: dict) -> None:
                """提供含碎片参数、多工具和续答的可控协议响应。"""
                calls = response.get("calls", [])
                text = response.get("text", "")
                if not self.path.endswith("/messages"):

                    def chunk(delta, finish=None):
                        self.event(
                            {
                                "id": "chatcmpl-tool",
                                "object": "chat.completion.chunk",
                                "created": 1,
                                "model": "test-model",
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": delta,
                                        "finish_reason": finish,
                                    }
                                ],
                            }
                        )

                    if text:
                        chunk({"content": text})
                    # 交错发送多个调用，并将名称和 JSON 均拆成片段。
                    for index, call in enumerate(calls):
                        chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": call["id"],
                                        "type": "function",
                                        "function": {
                                            "name": call["name"][:3],
                                            "arguments": "",
                                        },
                                    }
                                ]
                            }
                        )
                    for index, call in enumerate(calls):
                        raw = call["input"]
                        middle = len(raw) // 2
                        chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "function": {
                                            "name": call["name"][3:],
                                            "arguments": raw[:middle],
                                        },
                                    }
                                ]
                            }
                        )
                        chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "function": {"arguments": raw[middle:]},
                                    }
                                ]
                            }
                        )
                    chunk({}, "tool_calls" if calls else "stop")
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                def event(kind, **body):
                    self.event({"type": kind, **body}, kind)

                event(
                    "message_start",
                    message={
                        "id": "msg_tool",
                        "type": "message",
                        "role": "assistant",
                        "model": "test-model",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 0},
                    },
                )
                index = 0
                if text:
                    event(
                        "content_block_start",
                        index=index,
                        content_block={"type": "text", "text": ""},
                    )
                    event(
                        "content_block_delta",
                        index=index,
                        delta={"type": "text_delta", "text": text},
                    )
                    event("content_block_stop", index=index)
                    index += 1
                for call in calls:
                    event(
                        "content_block_start",
                        index=index,
                        content_block={
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["name"],
                            "input": {},
                        },
                    )
                    raw = call["input"]
                    for fragment in (raw[: len(raw) // 2], raw[len(raw) // 2 :]):
                        event(
                            "content_block_delta",
                            index=index,
                            delta={
                                "type": "input_json_delta",
                                "partial_json": fragment,
                            },
                        )
                    event("content_block_stop", index=index)
                    index += 1
                event(
                    "message_delta",
                    delta={
                        "stop_reason": "tool_use" if calls else "end_turn",
                        "stop_sequence": None,
                    },
                    usage={"output_tokens": 10},
                )
                event("message_stop")

            def openai(self) -> None:
                for text in [
                    "**你好**",
                    "\n\n- 已收到请求",
                    "\n\n```python\n",
                    "print('TermCode')\n```",
                ]:
                    self.event(
                        {
                            "id": "chatcmpl-test",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "test-model",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": text},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                    if owner.mode == "truncated":
                        return
                self.event(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "test-model",
                        "choices": [],
                    }
                )
                self.event(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "test-model",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"reasoning_content": "HIDDEN_THINKING"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def anthropic(self, body: dict) -> None:
                self.event(
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "model": "test-model",
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        },
                    },
                    "message_start",
                )
                index = 0
                if body.get("thinking"):
                    self.event(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {
                                "type": "thinking",
                                "thinking": "",
                                "signature": "",
                            },
                        },
                        "content_block_start",
                    )
                    self.event(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "thinking_delta",
                                "thinking": "HIDDEN_THINKING",
                            },
                        },
                        "content_block_delta",
                    )
                    self.event(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "signature_delta",
                                "signature": "signature-test",
                            },
                        },
                        "content_block_delta",
                    )
                    self.event(
                        {"type": "content_block_stop", "index": 0}, "content_block_stop"
                    )
                    index = 1
                self.event(
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "text", "text": ""},
                    },
                    "content_block_start",
                )
                for text in [
                    "**你好**",
                    "\n\n- 已收到请求",
                    "\n\n```python\n",
                    "print('TermCode')\n```",
                ]:
                    self.event(
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {"type": "text_delta", "text": text},
                        },
                        "content_block_delta",
                    )
                    if owner.mode == "truncated":
                        return
                self.event(
                    {"type": "content_block_stop", "index": index}, "content_block_stop"
                )
                self.event(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 10},
                    },
                    "message_delta",
                )
                self.event({"type": "message_stop"}, "message_stop")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def sse_server():
    server = SSEServer()
    server.start()
    try:
        yield server
    finally:
        server.close()
