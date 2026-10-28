"""终端文本安全和错误样式的行为验证。"""

from termcode.tui.view import clean_text, error_block, error_message


def test_control_characters_cannot_reconstruct_a_secret():
    assert clean_text("TEST_\x1bSECRET", ["TEST_SECRET"]) == "[已隐藏密钥]"
    assert clean_text("第一行\n\t第二行") == "第一行\n\t第二行"


def test_untrusted_error_body_is_not_displayed():
    class RemoteError(Exception):
        status_code = 401

    message = error_message(RemoteError("TEST_SECRET"))
    assert "TEST_SECRET" not in message
    assert "认证失败" in message
    assert error_block(message, 1.25).style == "bold red"
