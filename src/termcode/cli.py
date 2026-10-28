"""从当前项目配置启动 TermCode。"""

import sys

from termcode.config import ConfigError, load


def main() -> None:
    try:
        config = load(".termcode/config.yaml")
    except ConfigError as exc:
        print(f"TermCode: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    from termcode.tui.app import TermCodeApp
    from termcode.tui.keys import configure_terminal_keys

    configure_terminal_keys()
    app = TermCodeApp(config.providers)
    try:
        app.run(inline=True)
    except KeyboardInterrupt:
        pass
    except Exception:
        print("TermCode: 终端界面运行失败，请检查终端环境。", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        app.print_transcript()
