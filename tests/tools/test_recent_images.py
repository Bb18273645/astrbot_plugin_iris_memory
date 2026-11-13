"""最近图片记忆工具的回归测试。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot_plugin_iris_memory.iris_memory.tools.recent_images import (
    GetRecentImagesTool,
)

_MANAGER_PATCH = "astrbot_plugin_iris_memory.iris_memory.tools.recent_images.get_component_manager"
_PERSONA_PATCH = "astrbot_plugin_iris_memory.iris_memory.core.persona.resolve_persona"
_ADAPTER_PATCH = "astrbot_plugin_iris_memory.iris_memory.platform.get_adapter"


def _entry(
    content,
    minutes_ago=0,
    entry_type="image",
    group_id="",
    session_id="private:user-1",
):
    result = MagicMock()
    result.entry = MagicMock()
    result.entry.content = content
    result.entry.metadata = {
        "type": entry_type,
        "group_id": group_id,
        "session_id": session_id,
        "timestamp": (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(),
    }
    return result


def _make_manager(entries):
    l2 = MagicMock()
    l2._is_available = True
    l2.get_latest_memories = AsyncMock(return_value=entries)
    manager = MagicMock()
    manager.get_component.return_value = l2
    return manager


def _make_adapter(group_id="", session_id="private:user-1"):
    adapter = MagicMock()
    adapter.get_group_id.return_value = group_id
    adapter.get_session_id.return_value = session_id
    return adapter


def _context():
    context = MagicMock()
    context.context.event = MagicMock()
    return context


def _patches(manager, adapter):
    return (
        patch(_MANAGER_PATCH, return_value=manager),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
        patch(_ADAPTER_PATCH, return_value=adapter),
    )


@pytest.mark.asyncio
async def test_returns_recent_image_memories_only():
    entries = [
        _entry("图片记忆：u1 发送了一张图片，画面内容：橘猫", minutes_ago=1),
        _entry("用户喜欢喝咖啡", minutes_ago=2, entry_type="fact"),
        _entry("图片记忆：u1 发送了一张图片，画面内容：海边日落", minutes_ago=3),
    ]
    manager = _make_manager(entries)
    patches = _patches(manager, _make_adapter())

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=5, days=0)

    assert "2 条图片记忆" in output
    assert "橘猫" in output
    assert "海边日落" in output
    assert "咖啡" not in output


@pytest.mark.asyncio
async def test_other_chat_images_are_excluded_in_private_chat():
    entries = [
        _entry("图片记忆：自己的图", session_id="private:user-1"),
        _entry("图片记忆：别人的图", session_id="private:user-2"),
        _entry("图片记忆：群里的图", group_id="-100123", session_id="-100123"),
    ]
    manager = _make_manager(entries)
    patches = _patches(manager, _make_adapter())

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=5, days=0)

    assert "自己的图" in output
    assert "别人的图" not in output
    assert "群里的图" not in output


@pytest.mark.asyncio
async def test_other_group_images_are_excluded_in_group_chat():
    entries = [
        _entry("图片记忆：本群图", group_id="-100123", session_id="-100123"),
        _entry("图片记忆：别的群图", group_id="-100999", session_id="-100999"),
        _entry("图片记忆：私聊图", session_id="private:user-9"),
    ]
    manager = _make_manager(entries)
    group_adapter = _make_adapter(group_id="-100123", session_id="-100123")
    patches = _patches(manager, group_adapter)

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=5, days=0)

    assert "本群图" in output
    assert "别的群图" not in output
    assert "私聊图" not in output
    manager.get_component.return_value.get_latest_memories.assert_awaited()
    assert (
        manager.get_component.return_value.get_latest_memories.await_args.kwargs[
            "group_id"
        ]
        == "-100123"
    )


@pytest.mark.asyncio
async def test_respects_limit():
    entries = [_entry(f"图片记忆：图{i}", minutes_ago=i) for i in range(1, 6)]
    manager = _make_manager(entries)
    patches = _patches(manager, _make_adapter())

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=2, days=0)

    assert "图1" in output
    assert "图2" in output
    assert "图3" not in output


@pytest.mark.asyncio
async def test_days_filter_excludes_old_images():
    entries = [
        _entry("图片记忆：今天的图", minutes_ago=1),
        _entry("图片记忆：一个月前的图", minutes_ago=60 * 24 * 30),
    ]
    manager = _make_manager(entries)
    patches = _patches(manager, _make_adapter())

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=5, days=7)

    assert "今天的图" in output
    assert "一个月前的图" not in output


@pytest.mark.asyncio
async def test_none_days_falls_back_to_default_window():
    entries = [
        _entry("图片记忆：今天的图", minutes_ago=1),
        _entry("图片记忆：一个月前的图", minutes_ago=60 * 24 * 30),
    ]
    manager = _make_manager(entries)
    patches = _patches(manager, _make_adapter())

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=5, days=None)

    assert "今天的图" in output
    assert "一个月前的图" not in output


@pytest.mark.asyncio
async def test_negative_days_means_unlimited_scope():
    entries = [_entry("图片记忆：很久以前", minutes_ago=60 * 24 * 800)]
    manager = _make_manager(entries)
    patches = _patches(manager, _make_adapter())

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=5, days=-3)

    assert "很久以前" in output


@pytest.mark.asyncio
async def test_reports_empty_when_no_images():
    manager = _make_manager([_entry("普通记忆", entry_type="fact")])
    patches = _patches(manager, _make_adapter())

    with patches[0], patches[1], patches[2]:
        output = await GetRecentImagesTool().call(_context(), limit=5, days=7)

    assert "没有找到图片记录" in output
