"""可独立运行的最小 MCP server，用于真实管道和协议回退验收。"""

import json
import os
import sys
import time
from pathlib import Path

VERSION = "2026-07-28"


def response(message, *, legacy=False, paginate=False):
    method = message.get("method")
    if "id" not in message:
        return None
    envelope = {"jsonrpc": "2.0", "id": message["id"]}
    if method == "server/discover":
        if legacy:
            return envelope | {"error": {"code": -32601, "message": "Unknown method"}}
        result = {
            "supportedVersions": [VERSION],
            "capabilities": {"tools": {}},
            "resultType": "complete",
            "ttlMs": 0,
            "cacheScope": "private",
        }
    elif method == "initialize":
        result = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "termcode-fixture", "version": "1"},
        }
    elif method == "tools/list":
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
        result = {
            "tools": [
                {
                    "name": name,
                    "description": "回显输入文本",
                    "inputSchema": schema,
                    "annotations": {"readOnlyHint": name == "read_echo"},
                }
                for name in ("echo", "read_echo", "fail", "protocol_error")
            ],
            "resultType": "complete",
            "ttlMs": 0,
            "cacheScope": "private",
        }
        if paginate:
            if message.get("params", {}).get("cursor"):
                result["tools"] = result["tools"][2:]
            else:
                result["tools"] = result["tools"][:2]
                result["nextCursor"] = "second-page"
    elif method == "tools/call":
        params = message["params"]
        if params["name"] == "protocol_error":
            return envelope | {"error": {"code": -32603, "message": "fixture error"}}
        text = params.get("arguments", {}).get("text", "")
        if text == "ENV_PROBE":
            text = os.environ.get("MCP_TEST_VALUE", "")
        result = {
            "content": [{"type": "text", "text": text}],
            "isError": params["name"] == "fail",
            "resultType": "complete",
        }
    else:
        return envelope | {"error": {"code": -32601, "message": "Unknown method"}}
    return envelope | {"result": result}


def main():
    pid_file = os.environ.get("MCP_TEST_PID")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()))
    for line in sys.stdin:
        message = json.loads(line)
        if trace := os.environ.get("MCP_TEST_TRACE"):
            with Path(trace).open("a", encoding="utf-8") as output:
                output.write(json.dumps(message) + "\n")
        method = message.get("method")
        if method == "server/discover" and "--ignore-discover" in sys.argv:
            continue
        if method == "tools/list" and "--stall-list" in sys.argv:
            time.sleep(120)
        reply = response(
            message,
            legacy="--legacy" in sys.argv,
            paginate="--paginate" in sys.argv,
        )
        if reply is not None:
            print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
