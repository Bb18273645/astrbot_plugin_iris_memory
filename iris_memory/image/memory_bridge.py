"""把图片解析结果写入 L2 长期记忆。

图片描述除了进入 L1 上下文，还会作为一条独立的记忆条目落库，
这样后续对话里可以通过语义检索想起「用户之前发过的那张图」。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core import get_logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from ..core.components import ComponentManager
    from .models import ImageQueueItem

logger = get_logger("image.memory")


async def store_image_memory(
    component_manager: "ComponentManager",
    event: "AstrMessageEvent",
    img_item: Any,
    description: str,
) -> bool:
    """把一张图片的描述写入 L2 记忆库。

    Args:
        component_manager: 组件管理器实例
        event: 图片所属的 AstrBot 消息事件
        img_item: 图片队列条目（提供 image_hash / message_id / user_id）
        description: 图片解析得到的描述文本

    Returns:
        写入成功（或命中已存在的相似记忆）时返回 True，否则返回 False
    """
    from ..config import get_config

    config = get_config()
    if not config.get("image_memory_enable", True):
        logger.debug("图片长期记忆已关闭，跳过写入")
        return False

    description = (description or "").strip()
    if not description:
        return False

    max_chars = int(config.get("image_memory_max_chars", 400) or 0)
    if max_chars > 0 and len(description) > max_chars:
        description = description[:max_chars] + "…"

    l2_memory = component_manager.get_available_component("l2_memory")
    if not l2_memory:
        logger.debug("L2 记忆库不可用，跳过图片记忆写入")
        return False

    from datetime import datetime, timedelta

    from ..core.persona import resolve_persona
    from ..platform import get_adapter

    try:
        persona_id = await resolve_persona(component_manager, event)
        image_hash = str(getattr(img_item, "image_hash", "") or "")

        # 同一张图不重复写记忆：优先用 L2 的精确查询，旧适配器回退到扫描
        if image_hash:
            existing_id = None
            finder = getattr(l2_memory, "find_memory_id_by_metadata", None)
            if callable(finder):
                existing_id = await finder(
                    "image_hash", image_hash, persona_id=persona_id
                )
            else:
                dedupe_scan = int(config.get("image_memory_dedupe_scan", 500) or 0)
                if dedupe_scan > 0:
                    recent = await l2_memory.get_latest_memories(
                        limit=dedupe_scan,
                        persona_id=persona_id,
                    )
                    for result in recent:
                        metadata = result.entry.metadata or {}
                        if metadata.get("image_hash") == image_hash:
                            existing_id = result.entry.id
                            break
            if existing_id:
                logger.debug(f"图片记忆已存在，跳过：{image_hash[:16]}…")
                return True

        adapter = get_adapter(event)
        session_id = adapter.get_session_id(event)
        group_id = adapter.get_group_id(event)
        user_id = str(getattr(img_item, "user_id", "") or "") or adapter.get_user_id(
            event
        )
        message_id = str(getattr(img_item, "message_id", "") or "")

        sender = user_id or "用户"
        content = f"图片记忆：{sender} 发送了一张图片，画面内容：{description}"
        metadata = {
            "type": "image",
            "source": "image_parse",
            "image_hash": image_hash,
            "session_id": session_id,
            "group_id": group_id,
            "user_id": user_id,
            "message_id": message_id,
        }

        # 图片记忆按 TTL 过期：梦境周期里的 purge_expired_memories() 负责清理
        retention_days = int(config.get("image_memory_retention_days", 30) or 0)
        if retention_days > 0:
            metadata["expires_at"] = (
                datetime.now() + timedelta(days=retention_days)
            ).isoformat()

        memory_id = await l2_memory.add_memory(
            content,
            metadata,
            persona_id=persona_id,
        )
    except Exception as exc:
        logger.debug(f"写入图片记忆失败（已忽略）：{exc}")
        return False

    if memory_id:
        logger.debug(f"图片记忆已写入：{image_hash[:16]}… -> {memory_id}")
        return True
    logger.debug(f"图片记忆未写入（去重或不符条件）：{image_hash[:16]}…")
    return False
