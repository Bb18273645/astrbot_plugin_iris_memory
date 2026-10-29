"""
延迟定时任务注册的回归测试（#28 回归）

L2 改为 on_astrbot_loaded 后延迟初始化后，`_start_scheduled_tasks_deferred`
在「其余后台组件」结束时即被调用，此时 L2 尚未就绪。若按「L2 不可用就跳过」
注册，梦境任务会在整个进程生命周期内缺席。

说明：`iris_memory.config` 会 import `astrbot.api`，本测试不依赖 AstrBot 运行时，
因此用 sys.modules 桩替换该模块；lifecycle 内部是函数级 `from ..config import
get_config`，运行时解析到桩即可。
"""

import sys
import types
from unittest.mock import Mock

import pytest

from astrbot_plugin_iris_memory.iris_memory.core.lifecycle import (
    _start_scheduled_tasks_deferred,
)

_CONFIG_MODULE = "astrbot_plugin_iris_memory.iris_memory.config"
_DREAM_MODULE = "astrbot_plugin_iris_memory.iris_memory.dream"


class _StubDreamTask:
    """占位 DreamTask：只验证注册行为，不触碰 L2/numpy 依赖"""

    def __init__(self, component_manager):
        self._component_manager = component_manager

    async def execute(self) -> None:
        return None


@pytest.fixture
def fake_config(monkeypatch):
    """替换 iris_memory.config，返回可改写的桩模块"""
    pkg = sys.modules["astrbot_plugin_iris_memory.iris_memory"]
    module = types.ModuleType(_CONFIG_MODULE)
    module.get_config = lambda: None
    monkeypatch.setitem(sys.modules, _CONFIG_MODULE, module)
    monkeypatch.setattr(pkg, "config", module, raising=False)

    return module


@pytest.fixture(autouse=True)
def fake_dream(monkeypatch):
    """替换 iris_memory.dream，避免 import numpy/faiss"""
    pkg = sys.modules["astrbot_plugin_iris_memory.iris_memory"]
    module = types.ModuleType(_DREAM_MODULE)
    module.DreamTask = _StubDreamTask
    monkeypatch.setitem(sys.modules, _DREAM_MODULE, module)
    monkeypatch.setattr(pkg, "dream", module, raising=False)

    return module


def _make_manager(registered: dict, l2_status: str = "unavailable"):
    scheduler = Mock()
    scheduler.is_available = True

    def _register(task_name, coro_func, interval_hours):
        registered[task_name] = interval_hours

    scheduler.register_periodic_task.side_effect = _register

    manager = Mock()
    manager.get_component.side_effect = (
        lambda name: scheduler if name == "scheduler" else None
    )
    manager.check_component.return_value = l2_status
    return manager


def _make_config(**overrides) -> Mock:
    values = {
        "scheduled_tasks.enable_dream": True,
        "dream_task_interval_hours": 24,
        "learning.enable": False,
        "persona_evolution.enable": False,
    }
    values.update(overrides)
    config = Mock()
    config.get.side_effect = lambda key, default=None: values.get(key, default)
    return config


@pytest.mark.asyncio
async def test_dream_registered_even_if_l2_not_ready(fake_config):
    """L2 尚未就绪时仍要注册梦境任务，由 execute 在执行期守卫"""
    registered: dict = {}
    manager = _make_manager(registered, l2_status="unavailable")
    fake_config.get_config = lambda: _make_config()

    await _start_scheduled_tasks_deferred(manager)

    assert "dream" in registered
    assert registered["dream"] == 24


@pytest.mark.asyncio
async def test_dream_registered_when_l2_ready(fake_config):
    """L2 已就绪时行为不变"""
    registered: dict = {}
    manager = _make_manager(registered, l2_status="available")
    fake_config.get_config = lambda: _make_config()

    await _start_scheduled_tasks_deferred(manager)

    assert "dream" in registered


@pytest.mark.asyncio
async def test_dream_skipped_when_disabled(fake_config):
    """配置关闭时不注册"""
    registered: dict = {}
    manager = _make_manager(registered, l2_status="unavailable")
    fake_config.get_config = lambda: _make_config(
        **{"scheduled_tasks.enable_dream": False}
    )

    await _start_scheduled_tasks_deferred(manager)

    assert "dream" not in registered
