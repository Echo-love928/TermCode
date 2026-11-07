"""参数、空白和多行输入不得误执行零参数命令。"""

import pytest

from termcode.command import parse


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", ("", False)),
        ("   ", ("", False)),
        ("hello", ("", False)),
        ("/", ("", True)),
        ("/help", ("help", True)),
        ("  /HELP  ", ("help", True)),
        ("/help xx", ("", True)),
        ("/help  ", ("help", True)),
        ("//double", ("/double", True)),
        ("/ /help", ("", True)),
        ("/ help", ("", True)),
        ("/help\n/review", ("", True)),
        ("/help\txx", ("", True)),
    ],
)
def test_parse(text, expected):
    assert parse(text) == expected
