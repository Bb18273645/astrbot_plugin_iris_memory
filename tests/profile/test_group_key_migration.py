"""
画像存储键归一化与迁移测试（#29）

同一用户的画像按 group_id 分桶。开启「群画像隔离」后私聊链路的 group_id 是空串，
而管理器/Web 路由的默认值是哨兵 "default"，两者若不归一化会拆成两份画像。

本文件不依赖 AstrBot 运行时：优先用真实 config 模块，缺失时注入桩，
以便在裸 venv 中也能跑（容器内则走真实模块）。
"""

import copy
import sys
import types
from typing import Any, Dict, Optional

import pytest

_CONFIG_MODULE = "astrbot_plugin_iris_memory.iris_memory.config"


class _FakeConfig:
    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self._values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _install_config_stub_if_needed() -> None:
    """config 模块会 import astrbot.api，裸环境下用桩顶替（必须在 import storage 前）"""
    try:
        __import__(_CONFIG_MODULE)
        return
    except ModuleNotFoundError:
        pass

    pkg = sys.modules["astrbot_plugin_iris_memory.iris_memory"]
    module = types.ModuleType(_CONFIG_MODULE)
    module.get_config = lambda: _FakeConfig()
    sys.modules[_CONFIG_MODULE] = module
    pkg.config = module


_install_config_stub_if_needed()

from astrbot_plugin_iris_memory.iris_memory.profile.models import (  # noqa: E402
    UserProfile,
    profile_to_dict,
)
from astrbot_plugin_iris_memory.iris_memory.profile.storage import (  # noqa: E402
    ProfileStorage,
    effective_group_id,
)


class FakeKV:
    """最小 KV 存储：dict 语义 + 深拷贝，模拟 AstrBot 的 get/put/delete_kv_data"""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data: Dict[str, Any] = copy.deepcopy(data or {})

    async def get_kv_data(self, key: str, default: Any = None) -> Any:
        return copy.deepcopy(self.data.get(key, default))

    async def put_kv_data(self, key: str, value: Any) -> None:
        self.data[key] = copy.deepcopy(value)

    async def delete_kv_data(self, key: str) -> None:
        self.data.pop(key, None)


def _storage(kv: FakeKV) -> ProfileStorage:
    storage = ProfileStorage(kv)
    storage._is_available = True
    return storage


def _user_dict(
    user_id: str = "u1", *, version: int = 1, occupation: str = ""
) -> dict:
    profile = UserProfile(user_id=user_id, occupation=occupation)
    profile.version = version
    return profile_to_dict(profile)


def test_effective_group_id_normalizes_empty():
    assert effective_group_id("") == "default"
    assert effective_group_id(None) == "default"
    assert effective_group_id("   ") == "default"
    assert effective_group_id("group_1") == "group_1"


@pytest.mark.asyncio
async def test_save_with_empty_group_writes_default_key():
    kv = FakeKV()
    storage = _storage(kv)

    await storage.save_user_profile(UserProfile(user_id="u1"), group_id="")

    assert "user_profile:default:default:u1" in kv.data
    assert "user_profile:default::u1" not in kv.data
    assert kv.data["user_index:default:default"] == ["u1"]


@pytest.mark.asyncio
async def test_read_with_empty_group_hits_default_key():
    kv = FakeKV({"user_profile:default:default:u1": _user_dict(occupation="工程师")})
    storage = _storage(kv)

    profile = await storage.get_user_profile("u1", "")

    assert profile is not None
    assert profile.occupation == "工程师"


@pytest.mark.asyncio
async def test_migrate_merges_empty_group_profile_into_default():
    kv = FakeKV(
        {
            # 旧的空串 group 记录：有职业，其余为空
            "user_profile:default::u1": _user_dict(version=1, occupation="工程师"),
            # 新的 default 记录：空壳，版本更高
            "user_profile:default:default:u1": _user_dict(version=2),
            "user_index:default:": ["u1"],
            "user_index:default:default": ["u1"],
            "user_group_index:default": ["", "default"],
            "persona_index": ["default"],
        }
    )
    storage = _storage(kv)

    merged = await storage.migrate_empty_group_keys()

    assert merged == 1
    # 旧键删除，字段并入 default 记录
    assert "user_profile:default::u1" not in kv.data
    assert kv.data["user_profile:default:default:u1"]["occupation"] == "工程师"
    # 索引清理：空串 group 不再出现，空串索引被删除
    assert kv.data["user_group_index:default"] == ["default"]
    assert "user_index:default:" not in kv.data
    assert kv.data["user_index:default:default"] == ["u1"]


@pytest.mark.asyncio
async def test_migrate_is_idempotent():
    kv = FakeKV(
        {
            "user_profile:default::u1": _user_dict(),
            "user_index:default:": ["u1"],
            "user_group_index:default": [""],
        }
    )
    storage = _storage(kv)

    assert await storage.migrate_empty_group_keys() == 1
    assert await storage.migrate_empty_group_keys() == 0


@pytest.mark.asyncio
async def test_list_all_users_dedupes_legacy_empty_group():
    user = _user_dict()
    kv = FakeKV(
        {
            "user_profile:default:default:u1": user,
            "user_profile:default::u1": user,
            "user_index:default:default": ["u1"],
            "user_index:default:": ["u1"],
            "user_group_index:default": ["", "default"],
        }
    )
    storage = _storage(kv)

    users = await storage.list_all_users()

    assert len(users) == 1
    assert users[0]["user_id"] == "u1"
    assert users[0]["group_id"] == "default"


@pytest.mark.asyncio
async def test_migrate_alias_profiles_merges_display_name(monkeypatch):
    """显示名画像按 identity.user_id_aliases 并入真实 ID（#31）"""
    from astrbot_plugin_iris_memory.iris_memory.profile import storage as storage_module

    kv = FakeKV(
        {
            "user_profile:default:default:柠 夏": _user_dict("柠 夏", occupation="工程师"),
            "user_profile:default:default:8495766039": _user_dict(
                "8495766039", version=2
            ),
            "user_index:default:default": ["柠 夏", "8495766039"],
            "user_group_index:default": ["default"],
            "persona_index": ["default"],
        }
    )
    storage = _storage(kv)
    monkeypatch.setattr(
        storage_module,
        "get_config",
        lambda: _FakeConfig(
            {"identity.user_id_aliases": '{"柠 夏": "8495766039"}'}
        ),
    )

    merged = await storage.migrate_alias_profiles()

    assert merged == 1
    assert "user_profile:default:default:柠 夏" not in kv.data
    assert kv.data["user_profile:default:default:8495766039"]["occupation"] == "工程师"
    assert kv.data["user_index:default:default"] == ["8495766039"]

    # 幂等：再跑一次没有可合并项
    assert await storage.migrate_alias_profiles() == 0


@pytest.mark.asyncio
async def test_migrate_alias_profiles_noop_without_aliases(monkeypatch):
    from astrbot_plugin_iris_memory.iris_memory.profile import storage as storage_module

    kv = FakeKV({"user_profile:default:default:u1": _user_dict("u1")})
    storage = _storage(kv)
    monkeypatch.setattr(storage_module, "get_config", lambda: _FakeConfig({}))

    assert await storage.migrate_alias_profiles() == 0
