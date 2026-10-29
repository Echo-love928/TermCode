"""执行当前平台 shell；限制输出，取消时清理进程树。"""

import asyncio
import locale
import os
import signal
import sys
from typing import Any

from termcode.tool import TRUNCATED, Result, _truncate, arguments, string_schema


class BashTool:
    read_only = False

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return (
            "在当前工作目录执行 shell 命令（Unix sh / Windows cmd），返回输出及退出码。"
            "读文件、找文件、搜内容请优先用 read_file/glob/grep，不要用 bash 拼凑。"
        )

    def parameters(self) -> dict[str, Any]:
        return string_schema({"command": "要执行的 shell 命令"})

    async def execute(self, args: str) -> Result:
        try:
            data = arguments(args, self.parameters())
            proc = await asyncio.create_subprocess_shell(
                data["command"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
            try:
                stdout, stderr = await asyncio.gather(
                    self._drain(proc.stdout), self._drain(proc.stderr)
                )
                await proc.wait()
            finally:
                # shell 退出后子进程仍可能持有管道，必须清理整个进程组。
                if sys.platform != "win32":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif proc.returncode is None:
                    from termcode.tool.windows_process import kill_tree

                    await asyncio.to_thread(kill_tree, proc.pid)
                await proc.wait()
            return Result(
                "exit_code: "
                + str(proc.returncode)
                + "\nstdout:\n"
                + stdout
                + "\nstderr:\n"
                + stderr,
                is_error=proc.returncode != 0,
            )
        except Exception as exc:
            return Result(f"命令执行失败: {exc}", is_error=True)

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None) -> str:
        assert stream is not None
        kept = bytearray()
        total = 0
        while chunk := await stream.read(8192):
            total += len(chunk)
            kept.extend(chunk[: max(0, 14000 - len(kept))])
        text = kept.decode(locale.getpreferredencoding(False), errors="replace")
        text = _truncate(text, max_lines=5000, max_chars=14000)
        if total > len(kept) and not text.endswith(TRUNCATED):
            text += TRUNCATED
        return text
