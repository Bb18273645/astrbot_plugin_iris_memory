"""错误消息友好化的回归测试（重点是不要误伤正常回复）。"""

import logging
from unittest.mock import MagicMock

import pytest
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)

from astrbot_plugin_iris_memory.iris_memory.extras.error_friendly import (
    ErrorFriendlyMessages,
    ErrorFriendlyProcessor,
)


def _config(enabled: bool = True) -> MagicMock:
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: (
        enabled if key == "extras.error_friendly.enable" else default
    )
    return config


def _result(text: str, content_type=None) -> MessageEventResult:
    result = MessageEventResult()
    result.message(text)
    if content_type is not None:
        result.set_result_content_type(content_type)
    return result


def _event(extra=None) -> MagicMock:
    event = MagicMock()
    event.get_extra.return_value = extra
    return event


def _text(result: MessageEventResult) -> str:
    return result.get_plain_text()


def test_normal_reply_mentioning_api_error_is_not_replaced():
    """正常回复里讨论 API/平台/错误，不能触发替换"""
    processor = ErrorFriendlyProcessor(_config())
    text = (
        "这个 API 报错是因为平台返回了 500，错误信息里写了 upstream timeout。"
        "可以在 scripts/check.py 里加重试逻辑，再把 error 记录下来。"
    )
    result = _result(text)

    processor.process_result(result, _event())

    assert _text(result) == text


def test_llm_request_failure_is_friendly():
    processor = ErrorFriendlyProcessor(_config())
    result = _result("LLM 请求失败：timeout while connecting to provider")

    processor.process_result(result, _event())

    assert _text(result) == ErrorFriendlyMessages.NETWORK_ERROR_MSG


def test_unknown_llm_failure_is_default_message():
    processor = ErrorFriendlyProcessor(_config())
    result = _result("LLM 请求失败：upstream returned status code 502")

    processor.process_result(result, _event())

    assert _text(result) == ErrorFriendlyMessages.DEFAULT_FRIENDLY_MSG


def test_agent_exception_prefix_is_friendly():
    processor = ErrorFriendlyProcessor(_config())
    result = _result("Error occurred while processing agent request: boom")

    processor.process_result(result, _event())

    assert _text(result) == ErrorFriendlyMessages.DEFAULT_FRIENDLY_MSG


def test_framework_error_marker_block_is_friendly():
    processor = ErrorFriendlyProcessor(_config())
    result = _result("错误类型: ValueError\n错误信息: bad payload")

    processor.process_result(result, _event())

    assert _text(result) == ErrorFriendlyMessages.DEFAULT_FRIENDLY_MSG


def test_event_extra_marks_framework_error():
    """框架把错误文本写进 event extra 时，即使文本不像错误也要替换"""
    processor = ErrorFriendlyProcessor(_config())
    result = _result("随便一条框架错误文本")

    processor.process_result(result, _event(extra="LLM 请求失败：x"))

    assert _text(result) == ErrorFriendlyMessages.DEFAULT_FRIENDLY_MSG


def test_agent_runner_error_type_is_friendly():
    processor = ErrorFriendlyProcessor(_config())
    result = _result(
        "runner exploded",
        content_type=ResultContentType.AGENT_RUNNER_ERROR,
    )

    processor.process_result(result, _event())

    assert _text(result) == ErrorFriendlyMessages.DEFAULT_FRIENDLY_MSG


def test_disabled_config_keeps_original():
    processor = ErrorFriendlyProcessor(_config(enabled=False))
    result = _result("LLM 请求失败：timeout")

    processor.process_result(result, _event())

    assert _text(result) == "LLM 请求失败：timeout"


def test_original_error_is_logged_and_redacted(caplog):
    processor = ErrorFriendlyProcessor(_config())
    result = _result(
        "LLM 请求失败：Authorization Bearer secret-token-123 sk-abcdef1234567890"
    )

    with caplog.at_level(logging.WARNING):
        processor.process_result(result, _event())

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "拦截框架错误" in messages
    assert "sk-abcdef1234567890" not in messages
    assert "secret-token-123" not in messages
