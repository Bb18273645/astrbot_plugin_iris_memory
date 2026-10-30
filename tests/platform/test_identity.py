"""
用户身份归一化测试（#31）

部分平台把显示名当 user_id（内置 webchat），画像按 user_id 分桶会把同一个人
拆成多个。这里验证映射 / 兜底 / 关闭开关的行为。
"""

import sys
import types
from types import SimpleNamespace

import pytest

_CONFIG_MODULE = "astrbot_plugin_iris_memory.iris_memory.config"


def _install_config_stub_if_needed() -> None:
    try:
        __import__(_CONFIG_MODULE)
        return
    except ModuleNotFoundError:
        pass

    pkg = sys.modules["astrbot_plugin_iris_memory.iris_memory"]
    module = types.ModuleType(_CONFIG_MODULE)
    module.get_config = lambda: None
    sys.modules[_CONFIG_MODULE] = module
    pkg.config = module


_install_config_stub_if_needed()

from astrbot_plugin_iris_memory.iris_memory.identity import (  # noqa: E402
    parse_aliases,
    resolve_user_id,
)


def _generic_adapter_cls():
    """需要 AstrBot 运行时的适配器测试在裸 venv 里跳过"""
    pytest.importorskip("astrbot.api.message_components")
    from astrbot_plugin_iris_memory.iris_memory.platform.generic import (
        GenericAdapter,
    )

    return GenericAdapter


class FakeConfig:
    def __init__(self, **values):
        self._values = {
            "identity.enable": True,
            "identity.user_id_aliases": "",
            "identity.fallback_user_id": "",
            "identity.only_when_no_digits": True,
            "identity.fallback_platforms": "webchat",
        }
        self._values.update(values)

    def get(self, key, default=None):
        return self._values.get(key, default)


def test_parse_aliases_accepts_dict_and_json():
    assert parse_aliases({"柠 夏": "8495766039"}) == {"柠 夏": "8495766039"}
    assert parse_aliases('{"柠 夏": "8495766039"}') == {"柠 夏": "8495766039"}
    assert parse_aliases("") == {}


def test_parse_aliases_ignores_broken_json():
    assert parse_aliases("{not json") == {}
    assert parse_aliases('["a"]') == {}


def test_alias_wins_over_original_id():
    config = FakeConfig(**{"identity.user_id_aliases": '{"柠 夏": "8495766039"}'})

    assert resolve_user_id("柠 夏", nickname="柠 夏", config=config) == "8495766039"


def test_alias_applies_even_when_id_has_digits():
    config = FakeConfig(**{"identity.user_id_aliases": '{"12345": "67890"}'})

    assert resolve_user_id("12345", nickname="某人", config=config) == "67890"


def test_fallback_only_for_display_name_like_ids():
    config = FakeConfig(**{"identity.fallback_user_id": "8495766039"})

    # 与昵称相同 → 明显是显示名
    assert (
        resolve_user_id("柠 夏", nickname="柠 夏", platform="webchat", config=config)
        == "8495766039"
    )
    # 不含数字 → 也是显示名
    assert (
        resolve_user_id("Alice", nickname="爱丽丝", platform="webchat", config=config)
        == "8495766039"
    )
    # 正常平台 ID → 不动
    assert (
        resolve_user_id("8495766039", nickname="柠 夏", platform="webchat", config=config)
        == "8495766039"
    )


def test_fallback_disabled_by_only_when_no_digits():
    config = FakeConfig(
        **{
            "identity.fallback_user_id": "8495766039",
            "identity.only_when_no_digits": False,
        }
    )

    # 关闭该限制后，只要不是昵称相同就不改写
    assert (
        resolve_user_id("Alice", nickname="爱丽丝", platform="webchat", config=config)
        == "Alice"
    )


def test_fallback_scoped_to_platforms():
    """兜底默认只对 webchat 生效，避免字符串 userid 的平台被并成一个人"""
    config = FakeConfig(**{"identity.fallback_user_id": "8495766039"})

    assert resolve_user_id("Alice", platform="webchat", config=config) == "8495766039"
    assert resolve_user_id("Alice", platform="wecom", config=config) == "Alice"
    assert resolve_user_id("Alice", platform="", config=config) == "Alice"


def test_fallback_platform_wildcard():
    config = FakeConfig(
        **{
            "identity.fallback_user_id": "8495766039",
            "identity.fallback_platforms": "*",
        }
    )

    assert resolve_user_id("Alice", platform="wecom", config=config) == "8495766039"


def test_fallback_blank_platform_list_disables_fallback():
    config = FakeConfig(
        **{
            "identity.fallback_user_id": "8495766039",
            "identity.fallback_platforms": "",
        }
    )

    assert resolve_user_id("Alice", platform="webchat", config=config) == "Alice"


def test_no_fallback_configured_keeps_id():
    config = FakeConfig()

    assert resolve_user_id("柠 夏", nickname="柠 夏", config=config) == "柠 夏"


def test_disable_switch():
    config = FakeConfig(
        **{
            "identity.enable": False,
            "identity.user_id_aliases": '{"柠 夏": "8495766039"}',
            "identity.fallback_user_id": "8495766039",
        }
    )

    assert resolve_user_id("柠 夏", nickname="柠 夏", config=config) == "柠 夏"


def test_generic_adapter_applies_alias(monkeypatch):
    import astrbot_plugin_iris_memory.iris_memory.config as config_module

    monkeypatch.setattr(
        config_module,
        "get_config",
        lambda: FakeConfig(**{"identity.user_id_aliases": '{"柠 夏": "8495766039"}'}),
    )

    event = SimpleNamespace(
        message_obj=SimpleNamespace(
            sender=SimpleNamespace(user_id="柠 夏", nickname="柠 夏")
        )
    )

    assert _generic_adapter_cls()().get_user_id(event) == "8495766039"


def test_generic_adapter_keeps_real_id(monkeypatch):
    import astrbot_plugin_iris_memory.iris_memory.config as config_module

    monkeypatch.setattr(config_module, "get_config", lambda: FakeConfig())

    event = SimpleNamespace(
        message_obj=SimpleNamespace(
            sender=SimpleNamespace(user_id="8495766039", nickname="柠 夏")
        )
    )

    assert _generic_adapter_cls()().get_user_id(event) == "8495766039"


@pytest.mark.parametrize("raw", ["", None])
def test_empty_id_returned_as_is(raw):
    assert resolve_user_id(raw, config=FakeConfig()) == ""
