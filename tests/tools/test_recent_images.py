"""最近图片记忆工具的回归测试。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot_plugin_iris_memory.iris_memory.tools.recent_images import (
    GetRecentImagesTool,
)

_MANAGER_PATCH = "astrbot_plugin_iris_memory.iris_memory.tools.recent_images.get_component_manager"
_PERSONA_PATCH = "astrbot_plugin_iris_memory.iris_memory.core.persona.resolve_persona"


def _entry(content, minutes_ago=0, entry_type="image"):
    result = MagicMock()
    result.entry = MagicMock()
    result.entry.content = content
    result.entry.metadata = {
        "type": entry_type,
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


def _context():
    context = MagicMock()
    context.context.event = MagicMock()
    return context


@pytest.mark.asyncio
async def test_returns_recent_image_memories_only():
    entries = [
        _entry("图片记忆：u1 发送了一张图片，画面内容：橘猫", minutes_ago=1),
        _entry("用户喜欢喝咖啡", minutes_ago=2, entry_type="fact"),
        _entry("图片记忆：u1 发送了一张图片，画面内容：海边日落", minutes_ago=3),
    ]
    manager = _make_manager(entries)

    with (
        patch(_MANAGER_PATCH, return_value=manager),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        output = await GetRecentImagesTool().call(_context(), limit=5, days=0)

    assert "2 条图片记忆" in output
    assert "橘猫" in output
    assert "海边日落" in output
    assert "咖啡" not in output


@pytest.mark.asyncio
async def test_respects_limit():
    entries = [
        _entry(f"图片记忆：图{i}", minutes_ago=i) for i in range(1, 6)
    ]
    manager = _make_manager(entries)

    with (
        patch(_MANAGER_PATCH, return_value=manager),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
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

    with (
        patch(_MANAGER_PATCH, return_value=manager),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        output = await GetRecentImagesTool().call(_context(), limit=5, days=7)

    assert "今天的图" in output
    assert "一个月前的图" not in output


@pytest.mark.asyncio
async def test_reports_empty_when_no_images():
    manager = _make_manager([_entry("普通记忆", entry_type="fact")])

    with (
        patch(_MANAGER_PATCH, return_value=manager),
        patch(_PERSONA_PATCH, new_callable=AsyncMock, return_value="default"),
    ):
        output = await GetRecentImagesTool().call(_context(), limit=5, days=7)

    assert "没有找到图片记录" in output
