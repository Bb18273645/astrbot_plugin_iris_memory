"""
Iris Chat Memory - 用户身份归一化

背景（#31）：AstrBot 内置 webchat 适配器在 convert_message 里构造

    abm.sender = MessageMember(username, username)

第一个参数本该是平台原始 ID，这里直接用了显示名。于是按 user_id 分桶的
画像 / 记忆会把同一个人的 Web 面板会话与 Telegram 会话当成两个用户，
各存一份画像（一份还永远是空壳）。

本模块在平台适配器出口做一次归一化，规则按优先级：

1. ``identity.user_id_aliases``：显示名 -> 真实 ID 的显式映射，命中即替换。
   这是跨入口合并画像的唯一可靠依据（平台自己不会告诉我们哪两个 ID 是同一个人）。
2. ``identity.fallback_user_id``：未命中映射、且 ID 明显是显示名时（与 nickname
   完全相同，或不含任何数字）使用的兜底 ID。仅在配置了非空值时生效——
   默认留空，避免把所有未知用户并成一个人；``identity.fallback_platforms``
   进一步限定生效平台（默认仅 webchat），防止字符串 userid 的平台被误并。

配置示例（插件配置页「用户身份归一化」）::

    user_id_aliases: {"柠 夏": "8495766039"}
    fallback_user_id: ""
    only_when_no_digits: true
"""

import json
import re
from typing import Any, Dict, Optional

from .core import get_logger

logger = get_logger("platform.identity")

# 解析结果缓存：原文 -> 映射表（配置热更新时原文变化，缓存自然失效）
_alias_cache: Dict[str, Dict[str, str]] = {}
_alias_cache_source: Optional[str] = None

# 已记录过的映射，避免每条消息刷日志
_logged_mappings: set = set()


def parse_aliases(raw: Any) -> Dict[str, str]:
    """把配置里的别名解析成 dict

    同时接受 dict（代码/隐藏配置直接赋值）与 JSON 字符串（配置面板的文本框）。
    解析失败只记一次告警并返回空表，不影响消息链路。
    """
    global _alias_cache, _alias_cache_source

    if isinstance(raw, dict):
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip()}

    if not isinstance(raw, str):
        return {}

    text = raw.strip()
    if not text:
        return {}

    if text == _alias_cache_source:
        return _alias_cache

    mapping: Dict[str, str] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            mapping = {
                str(k).strip(): str(v).strip()
                for k, v in parsed.items()
                if str(k).strip() and str(v).strip()
            }
        else:
            logger.warning("identity.user_id_aliases 不是 JSON 对象，已忽略")
    except json.JSONDecodeError as e:
        logger.warning(f"identity.user_id_aliases 解析失败（{e}），已忽略")

    _alias_cache_source = text
    _alias_cache = mapping
    return mapping


def _parse_fallback_platforms(raw: Any) -> set:
    """解析允许使用兜底 ID 的平台白名单

    兜底会把「不含数字的 user_id」一律改写为同一个 ID。若不加平台限制，
    WeCom / 飞书这类用字符串 userid 的平台会把所有用户并成一个人，
    所以默认只对 webchat 生效（历史上唯一会把显示名当 ID 的平台）。
    空白配置表示不允许兜底，仅 "*" 表示不限制平台。
    """
    if isinstance(raw, (list, tuple, set)):
        items = [str(item) for item in raw]
    else:
        items = re.split(r"[,，\s]+", str(raw or ""))
    return {item.strip().lower() for item in items if item.strip()}


def _looks_like_display_name(uid: str, nickname: str, only_when_no_digits: bool) -> bool:
    """判断 user_id 是否明显是显示名而非平台原始 ID"""
    if nickname and uid == nickname:
        return True
    if only_when_no_digits and not any(ch.isdigit() for ch in uid):
        return True
    return False


def resolve_user_id(
    raw_id: Any, nickname: str = "", platform: str = "", config: Any = None
) -> str:
    """按配置归一化 user_id

    Args:
        raw_id: 适配器取到的原始 user_id
        nickname: 同一事件的昵称，用于识别「ID 就是显示名」的平台
        platform: 平台类型名（如 webchat / telegram），用于限定兜底范围
        config: 可注入的配置对象（测试用），默认取全局配置

    Returns:
        归一化后的 user_id；未命中任何规则时原样返回
    """
    uid = str(raw_id or "").strip()
    if not uid:
        return uid

    if config is None:
        from .config import get_config

        try:
            config = get_config()
        except Exception:  # 配置未初始化时不影响消息链路
            return uid

    if not config.get("identity.enable", True):
        return uid

    aliases = parse_aliases(config.get("identity.user_id_aliases", ""))
    target = aliases.get(uid)
    if not target:
        fallback = str(config.get("identity.fallback_user_id", "") or "").strip()
        allowed = _parse_fallback_platforms(
            config.get("identity.fallback_platforms", "webchat")
        )
        platform_ok = "*" in allowed or platform.strip().lower() in allowed
        if (
            fallback
            and platform_ok
            and _looks_like_display_name(
                uid, nickname, bool(config.get("identity.only_when_no_digits", True))
            )
        ):
            target = fallback

    if not target or target == uid:
        return uid

    if (uid, target) not in _logged_mappings:
        _logged_mappings.add((uid, target))
        logger.info(f"用户身份归一化：{uid} -> {target}")
    return target
