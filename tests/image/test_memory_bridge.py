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


def _l2_memory(existing=None, memory_id="mem_123"):
    l2_memory = MagicMock()
    l2_memory.add_memory = AsyncMock(return_value=memory_id)
    l2_memory.get_latest_memories = AsyncMock(return_value=existing or [])
    return l2_memory


def _item(image_hash="ph:abcdef012345"):
    item = MagicMock()
    item.image_hash = image_hash
    item.message_id = "m1"
    item.user_id = "user-1"
    return item


def _recent_entry(image_hash):
    result = MagicMock()
    result.entry = MagicMock()
    result.entry.metadata = {"type": "image", "image_hash": image_hash}
    result.entry.content = "图片记忆：user-1 发送了一张图片"
    return result


def _adapter():
    adapter = MagicMock()
    adapter.get_session_id.return_value = "private:user-1"
    adapter.get_user_id.return_value = "user-1"
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
    assert l2_memory.add_memory.await_args.kwargs["persona_id"] == "default"


@pytest.mark.asyncio
async def test_same_image_is_not_written_twice():
    l2_memory = _l2_memory(existing=[_recent_entry("ph:abcdef012345")])
    manager = _component_manager(l2_memory)

    with (
        patch(_CONFIG_PATCH, return_value=_config()),
        patch(_ADAPTER_PATCH, return_value=_adapter()),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        ok = await store_image_memory(manager, MagicMock(), _item(), "一只橘猫")

    assert ok is True
    l2_memory.add_memory.assert_not_awaited()


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
