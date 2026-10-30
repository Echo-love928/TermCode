"""从当前项目配置启动 TermCode。"""

import sys
from pathlib import Path

from termcode.config import ConfigError, load


def main() -> None:
    if sys.argv[1:] == ["--version"]:
        from termcode import __version__

        print(f"TermCode {__version__}")
        return
    try:
        config = load(".termcode/config.yaml")
    except ConfigError as exc:
        print(f"TermCode: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    from termcode.permission import new_engine
    from termcode.tool import new_default_registry
    from termcode.tui.app import TermCodeApp
    from termcode.tui.keys import configure_terminal_keys

    configure_terminal_keys()
    engine, err = new_engine(str(Path.cwd()))
    if err is not None:
        print("权限引擎降级：无法解析项目根目录", file=sys.stderr)
    app = TermCodeApp(config.providers, registry=new_default_registry(), engine=engine)
    try:
        app.run(inline=True)
    except KeyboardInterrupt:
        pass
    except Exception:
        print("TermCode: 终端界面运行失败，请检查终端环境。", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        app.print_transcript()
