"""错误消息友好化处理器

在消息发送前拦截框架错误消息，替换为友好提示。
（自 v2 processing/message_processor.py 摘出，独立保留）

检测必须严格：早期实现用「包含 error 关键词 + 包含 api/platform 字样」
这种启发式，会把正常回复里讨论 API 报错、平台异常的内容误判成框架错误，
整条回复被替换成「呜...遇到了一点问题」。现在改为：
1. 事件上带有框架写入的 LLM 错误标记；
2. 结果类型为 AGENT_RUNNER_ERROR；
3. 文本以已知的框架错误前缀开头；
4. 文本包含两个以上框架错误块的固定标记（错误类型/错误信息/请在平台日志查看）。
"""

import re
from typing import Any, Final

from astrbot.api.event import AstrMessageEvent

from ..core import get_logger

try:  # 宿主版本差异时回退到已知的 extra key
    from astrbot.core.astr_main_agent import LLM_ERROR_MESSAGE_EXTRA_KEY
except Exception:  # pragma: no cover - 仅兼容旧宿主
    LLM_ERROR_MESSAGE_EXTRA_KEY = "_llm_error_message"

logger = get_logger("extras.error_friendly")


class ErrorFriendlyMessages:
    """错误消息友好化配置"""

    # 框架错误文本的固定前缀（小写比较）
    ERROR_PREFIXES: Final[tuple[str, ...]] = (
        "astrbot 请求失败",
        "llm 请求失败",
        "llm request failed",
        "error occurred while processing agent",
        "coze 请求失败",
        "dify 请求失败",
        "阿里云百炼请求失败",
    )

    # 框架错误块里的固定字段，出现两个以上才认为是真错误
    ERROR_MARKERS: Final[tuple[str, ...]] = (
        "错误类型:",
        "错误信息:",
        "请在平台日志查看",
    )

    DEFAULT_FRIENDLY_MSG: Final[str] = "呜...遇到了一点问题，请稍后再试试吧~"
    NETWORK_ERROR_MSG: Final[str] = "网络好像不太稳定呢，稍后再试试？"
    RATE_LIMIT_MSG: Final[str] = "我需要休息一下，请稍后再来找我~"
    BAD_REQUEST_MSG: Final[str] = "请求出了点问题，稍后再试试吧~"


class ErrorFriendlyProcessor:
    """错误消息友好化处理器

    在消息发送前拦截框架错误消息，替换为友好提示。

    Args:
        config: 插件配置对象（iris_memory.config.Config）
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    def should_process(self, event: AstrMessageEvent) -> bool:
        """检查是否需要处理该事件"""
        return self._is_enabled()

    def process_result(self, result: Any, event: Any = None) -> None:
        """处理消息结果，替换错误消息

        Args:
            result: AstrBot 的消息结果对象
            event: 当前事件，用于读取框架写入的 LLM 错误标记
        """
        if not result:
            return
        if not self._is_enabled():
            return

        text = self._get_result_plain_text(result)
        if not text:
            return

        if not self._is_framework_error(text, result, event):
            return

        friendly_msg = self._get_friendly_error_message(text)
        logger.warning(
            "[error_friendly] 拦截框架错误并替换为友好提示：%s", self._redact(text)
        )
        result.chain.clear()
        result.message(friendly_msg)

    def _is_enabled(self) -> bool:
        """检查错误消息友好化功能是否启用"""
        try:
            if hasattr(self._config, "get"):
                return bool(self._config.get("extras.error_friendly.enable", True))
            return True
        except Exception:
            return True

    def _is_framework_error(
        self,
        text: str,
        result: Any = None,
        event: Any = None,
    ) -> bool:
        """检测是否为 AstrBot 框架错误消息（严格匹配，避免误伤正常回复）"""
        # 1) 框架在处理 LLM 错误时会把错误文本写进 event extra
        if event is not None:
            try:
                if event.get_extra(LLM_ERROR_MESSAGE_EXTRA_KEY):
                    return True
            except Exception:
                pass

        # 2) 第三方 Agent Runner 的错误结果带明确类型
        content_type = getattr(result, "result_content_type", None)
        if getattr(content_type, "name", "") == "AGENT_RUNNER_ERROR":
            return True

        normalized = text.strip().lower()

        # 3) 已知框架错误前缀
        if normalized.startswith(ErrorFriendlyMessages.ERROR_PREFIXES):
            return True

        # 4) 框架错误块的固定字段（至少两个）
        marker_hits = sum(
            1
            for marker in ErrorFriendlyMessages.ERROR_MARKERS
            if marker in normalized
        )
        return marker_hits >= 2

    def _get_friendly_error_message(self, text: str) -> str:
        """根据错误内容返回合适的友好消息"""
        text_lower = text.lower()

        if "400" in text or "bad request" in text_lower:
            return ErrorFriendlyMessages.BAD_REQUEST_MSG

        if any(kw in text_lower for kw in ["network", "timeout", "连接", "网络"]):
            return ErrorFriendlyMessages.NETWORK_ERROR_MSG

        if any(kw in text_lower for kw in ["rate", "limit", "限流", "频率", "频繁"]):
            return ErrorFriendlyMessages.RATE_LIMIT_MSG

        return ErrorFriendlyMessages.DEFAULT_FRIENDLY_MSG

    @staticmethod
    def _redact(text: str) -> str:
        """日志里隐藏可能的密钥，并截断过长错误"""
        cleaned = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", text)
        cleaned = re.sub(
            r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", cleaned
        )
        return cleaned[:400]

    def _get_result_plain_text(self, result: Any) -> str:
        """获取消息结果的纯文本内容"""
        if hasattr(result, "get_plain_text"):
            return result.get_plain_text() or ""
        return ""
