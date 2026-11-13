"""Fork 自有功能的守卫测试。

这些断言不依赖 AstrBot 运行时，只检查仓库里的关键实现是否还在。
每次同步上游后运行；任何一项失败都说明 Fork 的自有功能被覆盖了。
"""

from pathlib import Path

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
    assert "get_latest_memories(" in source, "同一张图的去重扫描被删除了"
    assert "expires_at" in source, "图片记忆的 TTL 过期时间被删除了"


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
