"""图片长期记忆写入的回归测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot_plugin_iris_memory.iris_memory.image.memory_bridge import (
    store_image_memory,
)

_CONFIG_PATCH = "astrbot_plugin_iris_memory.iris_memory.config.get_config"
_ADAPTER_PATCH = "astrbot_plugin_iris_memory.iris_memory.platform.get_adapter"
_PERSONA_PATCH = "astrbot_plugin_iris_memory.iris_memory.core.persona.resolve_persona"


def _config(**overrides):
    values = {
        "image_memory_enable": True,
        "image_memory_max_chars": 400,
        "image_memory_retention_days": 30,
        "image_memory_dedupe_scan": 500,
    }
    values.update(overrides)
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: values.get(key, default)
    return config


def _component_manager(l2_memory):
    manager = MagicMock()
    manager.get_available_component.side_effect = lambda name: (
        l2_memory if name == "l2_memory" else None
    )
    return manager


def _l2_memory(memory_id="mem_123", existing_id=None):
    l2_memory = MagicMock()
    l2_memory.add_memory = AsyncMock(return_value=memory_id)
    l2_memory.get_latest_memories = AsyncMock(return_value=[])
    l2_memory.find_memory_id_by_metadata = AsyncMock(return_value=existing_id)
    return l2_memory


def _item(image_hash="ph:abcdef012345"):
    item = MagicMock()
    item.image_hash = image_hash
    item.message_id = "m1"
    item.user_id = "user-1"
    return item


def _adapter(group_id=""):
    adapter = MagicMock()
    adapter.get_session_id.return_value = (
        f"group:{group_id}" if group_id else "private:user-1"
    )
    adapter.get_user_id.return_value = "user-1"
    adapter.get_group_id.return_value = group_id
    return adapter


@pytest.mark.asyncio
async def test_image_description_is_written_to_l2():
    l2_memory = _l2_memory()
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config()),
        patch(_ADAPTER_PATCH, return_value=_adapter()),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        ok = await store_image_memory(manager, MagicMock(), _item(), "一只橘猫坐在键盘上")

    assert ok is True
    l2_memory.add_memory.assert_awaited_once()
    content = l2_memory.add_memory.await_args.args[0]
    metadata = l2_memory.add_memory.await_args.args[1]
    assert "一只橘猫坐在键盘上" in content
    assert "user-1" in content
    assert metadata["type"] == "image"
    assert metadata["image_hash"] == "ph:abcdef012345"
    assert metadata["session_id"] == "private:user-1"
    assert metadata["group_id"] == ""
    assert l2_memory.add_memory.await_args.kwargs["persona_id"] == "default"
    # 去重已按 (persona, image_hash, session) 精确完成，写入时关闭语义去重，
    # 否则同一张图发到另一个会话时会被相似度合并吞掉
    assert l2_memory.add_memory.await_args.kwargs["skip_dedup"] is True


@pytest.mark.asyncio
async def test_group_id_is_recorded_for_group_chats():
    l2_memory = _l2_memory()
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config()),
        patch(_ADAPTER_PATCH, return_value=_adapter(group_id="-100123")),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        await store_image_memory(manager, MagicMock(), _item(), "群里的图")

    metadata = l2_memory.add_memory.await_args.args[1]
    assert metadata["group_id"] == "-100123"


@pytest.mark.asyncio
async def test_same_image_is_not_written_twice():
    l2_memory = _l2_memory(existing_id="mem_existing")
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config()),
        patch(_ADAPTER_PATCH, return_value=_adapter()),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        ok = await store_image_memory(manager, MagicMock(), _item(), "一只橘猫")

    assert ok is True
    finder_args = l2_memory.find_memory_id_by_metadata.await_args
    assert finder_args.args[:2] == ("image_hash", "ph:abcdef012345")
    assert finder_args.kwargs["session_id"] == "private:user-1"
    l2_memory.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_image_in_another_chat_is_written_again():
    """同一张图发到另一个会话时仍要各写一条，否则第二个会话查不到"""
    l2_memory = _l2_memory(existing_id=None)
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config()),
        patch(_ADAPTER_PATCH, return_value=_adapter(group_id="-100999")),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        ok = await store_image_memory(manager, MagicMock(), _item(), "一只橘猫")

    assert ok is True
    l2_memory.add_memory.assert_awaited_once()
    assert l2_memory.add_memory.await_args.kwargs["skip_dedup"] is True
    # 查询会带上当前会话，避免命中别的会话同一张图的记录
    finder_args = l2_memory.find_memory_id_by_metadata.await_args
    assert finder_args.kwargs["session_id"] == "group:-100999"


@pytest.mark.asyncio
async def test_retention_sets_expiry():
    l2_memory = _l2_memory()
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config(image_memory_retention_days=7)),
        patch(_ADAPTER_PATCH, return_value=_adapter()),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        await store_image_memory(manager, MagicMock(), _item(), "一只橘猫")

    metadata = l2_memory.add_memory.await_args.args[1]
    assert "expires_at" in metadata


@pytest.mark.asyncio
async def test_retention_zero_keeps_forever():
    l2_memory = _l2_memory()
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config(image_memory_retention_days=0)),
        patch(_ADAPTER_PATCH, return_value=_adapter()),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        await store_image_memory(manager, MagicMock(), _item(), "一只橘猫")

    metadata = l2_memory.add_memory.await_args.args[1]
    assert "expires_at" not in metadata


@pytest.mark.asyncio
async def test_disabled_flag_skips_write():
    l2_memory = _l2_memory()
    manager = _component_manager(l2_memory)

    with patch(_CONFIG_PATCH, return_value=_config(image_memory_enable=False)):
        ok = await store_image_memory(manager, MagicMock(), _item(), "描述")

    assert ok is False
    l2_memory.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_l2_component_skips_write():
    manager = _component_manager(None)

    with (
        patch(_CONFIG_PATCH, return_value=_config()),
        patch(_ADAPTER_PATCH, return_value=MagicMock()),
    ):
        ok = await store_image_memory(manager, MagicMock(), _item(), "描述")

    assert ok is False


@pytest.mark.asyncio
async def test_long_description_is_truncated():
    l2_memory = _l2_memory()
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config(image_memory_max_chars=10)),
        patch(_ADAPTER_PATCH, return_value=_adapter()),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        await store_image_memory(manager, MagicMock(), _item(), "很" * 50)

    content = l2_memory.add_memory.await_args.args[0]
    assert "很" * 10 + "…" in content
    assert "很" * 11 not in content
