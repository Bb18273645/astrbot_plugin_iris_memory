"""查看最近图片记忆的 LLM Tool"""

from datetime import datetime, timedelta
from typing import Any, Optional

from pydantic import Field
from pydantic.dataclasses import dataclass
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core import get_logger, get_component_manager
from ..l2_memory.adapter import L2MemoryAdapter

logger = get_logger("tools")

_IMAGE_MEMORY_PREFIX = "图片记忆："


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """把记忆里的时间字段解析成 naive 本地时间，失败返回 None。"""
    parsed: Optional[datetime] = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed is not None and parsed.tzinfo is not None:
        # 统一成 naive 本地时间，避免与 datetime.now() 比较时报错
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _normalize_days(raw: Any) -> int:
    """把模型传入的 days 收敛到 0..3650；None/非法值回落到默认 7 天。"""
    if raw is None or raw == "":
        return 7
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 7
    return max(0, min(days, 3650))


@dataclass
class GetRecentImagesTool(FunctionTool[AstrAgentContext]):
    """查看最近图片记忆的 Tool

    用户问「我之前发的图」「最近有什么图片」时，从 L2 记忆里
    按时间倒序取出图片描述，让模型能基于真实记录回答。
    """

    name: str = "get_recent_images"
    description: str = (
        "查看最近的图片记忆（用户发过的图片及其画面描述），"
        "按时间倒序返回。用户问「我之前发的那张图」「最近有什么图片」时使用。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回的图片条数（默认 5，最多 20）",
                    "default": 5,
                },
                "days": {
                    "type": "integer",
                    "description": "只看最近多少天的图片（默认 7，0 表示不限）",
                    "default": 7,
                },
            },
            "required": [],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        try:
            limit = max(1, min(int(kwargs.get("limit", 5) or 5), 20))
            days = _normalize_days(kwargs.get("days", 7))

            event = context.context.event
            manager = get_component_manager()
            l2_adapter = manager.get_component("l2_memory", L2MemoryAdapter)

            if not l2_adapter or not l2_adapter._is_available:
                return "L2记忆库当前不可用"

            from ..core.persona import resolve_persona
            from ..platform import get_adapter

            persona_id = await resolve_persona(manager, event)
            adapter = get_adapter(event)
            group_id = adapter.get_group_id(event)
            session_id = adapter.get_session_id(event)

            # 多取一些再过滤：图片记忆只占普通记忆的一部分
            scan_limit = max(limit * 20, 100)
            entries = await l2_adapter.get_latest_memories(
                limit=scan_limit,
                group_id=group_id or None,
                persona_id=persona_id,
            )

            cutoff = datetime.now() - timedelta(days=days) if days > 0 else None
            selected = []
            for result in entries:
                metadata = result.entry.metadata or {}
                if metadata.get("type") != "image":
                    continue
                # 只返回当前会话的图片，避免把私聊/其它群的图片带进本群
                if group_id:
                    if metadata.get("group_id") != group_id:
                        continue
                elif session_id and metadata.get("session_id") != session_id:
                    continue
                sent_at = _parse_timestamp(
                    metadata.get("timestamp") or result.entry.timestamp
                )
                if cutoff is not None:
                    if sent_at is None or sent_at < cutoff:
                        continue
                selected.append((sent_at, result.entry))
                if len(selected) >= limit:
                    break

            if not selected:
                scope = f"最近 {days} 天" if days > 0 else "记忆库里"
                return f"{scope}没有找到图片记录"

            lines = [f"找到 {len(selected)} 条图片记忆（按时间倒序）："]
            for index, (sent_at, entry) in enumerate(selected, 1):
                content = entry.content or ""
                if content.startswith(_IMAGE_MEMORY_PREFIX):
                    content = content[len(_IMAGE_MEMORY_PREFIX) :]
                when = sent_at.strftime("%Y-%m-%d %H:%M") if sent_at else "时间未知"
                lines.append(f"{index}. [{when}] {content}")

            logger.info(f"LLM 查询最近图片: persona={persona_id}, results={len(selected)}")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"查询最近图片失败：{e}", exc_info=True)
            return f"查询最近图片失败：{str(e)}"
