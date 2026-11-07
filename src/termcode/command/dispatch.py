"""只识别无参数的斜杠命令，不解释参数或执行任何动作。"""


def parse(input_text: str) -> tuple[str, bool]:
    text = input_text.strip()
    if not text.startswith("/"):
        return "", False
    name = text[1:]
    if not name or any(c.isspace() for c in name):
        return "", True
    return name.lower(), True
