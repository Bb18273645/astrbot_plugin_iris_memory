"""Fork 自有功能的守卫测试。

这些断言不依赖 AstrBot 运行时，只检查仓库里的关键实现是否还在。
每次同步上游后运行；任何一项失败都说明 Fork 的自有功能被覆盖了。
"""

from pathlib import Path

import re

ROOT = Path(__file__).resolve().parents[2]

REQUEST_HOOK = "iris_memory/core/llm_request_hook.py"
RESPONSE_HOOK = "iris_memory/core/llm_response_hook.py"
METADATA = "metadata.yaml"
FORK_REPO = "github.com/Bb18273645/astrbot_plugin_iris_memory"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def _function_body(source: str, marker: str) -> str:
    """返回顶层函数从 marker 到下一个顶层 def 之间的源码。"""
    start = source.index(marker) + len(marker)
    rest = source[start:]
    endings = [rest.find("\n\nasync def "), rest.find("\n\ndef ")]
    valid = [index for index in endings if index != -1]
    return rest[: min(valid)] if valid else rest


def test_shared_request_budget_is_wired():
    source = _read(REQUEST_HOOK)
    assert "def _apply_request_budget(" in source, "统一上下文预算函数被删除了"
    preprocess = _function_body(source, "async def preprocess_llm_request(")
    assert "_apply_request_budget(" in preprocess, "统一上下文预算没有在请求预处理里调用"


def test_image_backfill_is_wired():
    source = _read(REQUEST_HOOK)
    assert "async def _build_image_map(" in source, "图片描述映射函数被删除了"
    assert "def _get_inline_image_desc(" in source, "行内图片描述函数被删除了"
    l1_collect = _function_body(source, "async def _collect_l1_context(")
    assert "_build_image_map(" in l1_collect, "图片描述映射没有接回 L1 上下文收集"
    assert "_get_inline_image_desc(" in l1_collect, "L1 上下文没有使用行内图片描述"


def test_current_turn_image_context_is_wired():
    source = _read(REQUEST_HOOK)
    assert (
        "async def _collect_current_turn_image_context(" in source
    ), "本轮图片上下文函数被删除了"
    preprocess = _function_body(source, "async def preprocess_llm_request(")
    assert (
        "_collect_current_turn_image_context(" in preprocess
    ), "本轮图片上下文没有接入请求预处理"


def test_image_memory_is_wired():
    bridge = _read("iris_memory/image/memory_bridge.py")
    assert "async def store_image_memory(" in bridge, "图片记忆写入函数被删除了"
    assert "store_image_memory(" in _read(REQUEST_HOOK), "related 模式没有写入图片记忆"
    assert "store_image_memory(" in _read("iris_memory/core/message_hook.py"), (
        "all 模式没有写入图片记忆"
    )


def test_image_memory_dedupe_and_retention():
    source = _read("iris_memory/image/memory_bridge.py")
    assert "find_memory_id_by_metadata" in source, "图片记忆的精确去重查询被删除了"
    assert "session_id=session_id" in source, "图片记忆去重没有限定会话"
    assert "skip_dedup=True" in source, "图片记忆写入没有关闭语义去重"
    assert "expires_at" in source, "图片记忆的 TTL 过期时间被删除了"


def test_telegram_temp_files_are_cleaned():
    source = _read("iris_memory/core/message_hook.py")
    assert "_remove_prepared_image_file" in source, "下载临时文件的清理逻辑被删除了"


def test_image_memory_is_scoped_to_chat():
    source = _read("iris_memory/image/memory_bridge.py")
    assert '"group_id": group_id' in source, "图片记忆没有记录群 ID"
    tool = _read("iris_memory/tools/recent_images.py")
    assert "session_id" in tool, "最近图片工具没有按会话过滤"
    assert "_normalize_days(" in tool, "days 参数没有做范围收敛"


def test_telegram_adapter_is_registered():
    factory = _read("iris_memory/platform/factory.py")
    assert "TelegramAdapter" in factory, "Telegram 适配器没有注册"
    assert '"telegram"' in factory, "Telegram 平台键被删除了"
    adapter = _read("iris_memory/platform/telegram.py")
    assert "async def prepare_images(" in adapter, "Telegram 图片下载入口被删除了"


def test_platform_prepare_images_is_wired():
    assert "async def prepare_images(" in _read("iris_memory/platform/base.py")
    assert "prepare_images(" in _read("iris_memory/core/message_hook.py"), (
        "图片入队没有调用平台图片预处理"
    )
    assert "image_info_has_source" in _read("iris_memory/core/llm_request_hook.py"), (
        "图片解析没有接受本地文件来源"
    )


def test_error_friendly_stays_strict():
    source = _read("iris_memory/extras/error_friendly.py")
    assert "LLM_ERROR_MESSAGE_EXTRA_KEY" in source, "框架错误标记检测被删除了"
    assert "ERROR_PREFIXES" in source, "框架错误前缀匹配被删除了"
    assert "has_framework_indicator" not in source, "宽松的错误启发式又回来了"


def test_recent_images_tool_registered():
    registry = _read("iris_memory/tools/registry.py")
    assert "GetRecentImagesTool" in registry, "最近图片工具没有注册"
    assert "get_recent_images" in registry, "最近图片工具名被删除了"
    assert "get_recent_images" in _read("iris_memory/tools/recent_images.py")


def test_summary_strips_image_placeholders():
    source = _read("iris_memory/l1_buffer/summarizer.py")
    assert "_clean_image_placeholders" in source, "摘要前的图片占位符清理被删除了"


def test_l1_dedupe_is_wired():
    source = _read(REQUEST_HOOK)
    assert "def _dedupe_l1_messages(" in source, "L1 重复回复去重函数被删除了"
    l1_collect = _function_body(source, "async def _collect_l1_context(")
    assert "_dedupe_l1_messages(messages)" in l1_collect, "L1 注入前没有做重复回复去重"


def test_response_dedupe_is_present():
    source = _read(RESPONSE_HOOK)
    assert "iris_l1_response_digest" in source, "同一事件重复响应的去重逻辑被删除了"


def test_metadata_points_to_this_fork():
    assert FORK_REPO in _read(METADATA), "metadata.yaml 的仓库地址被改回了上游"


def test_metadata_version_uses_fork_scheme():
    metadata = _read(METADATA)
    match = re.search(r"^version:\s*(\S+)", metadata, re.MULTILINE)
    assert match, "metadata.yaml 缺少 version 字段"
    raw = match.group(1)
    version = raw.strip("\"'")
    assert raw[:1] in "\"'", (
        "version 必须加引号：YAML 会把 1.00 解析成浮点数 1.0，"
        "AstrBot 更新时校验「version 必须是非空字符串」会直接失败"
    )
    assert re.fullmatch(r"\d+\.\d{2}(\.\d+)?", version), (
        f"版本号 {version} 不符合 Fork 规则：功能更新 +0.1（1.00 → 1.10），"
        "修复 +0.0.1（1.00 → 1.00.1），大版本才进整数位"
    )
    assert not version.startswith("3."), "插件版本号不要沿用上游 3.x"
    assert version in _read("README.md"), "README 没有引用 metadata.yaml 里的当前版本号"
