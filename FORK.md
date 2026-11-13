# Fork 维护说明

这个仓库是 [Iris Memory](https://github.com/leafliber/astrbot_plugin_iris_memory) 的个人分支，
在作者版本之上维护自己的功能。目标是：**上游更新随时能同步，自己的功能不允许被覆盖**。

## 仓库结构

| 远程 | 地址 | 用途 |
| --- | --- | --- |
| `origin` | `Bb18273645/astrbot_plugin_iris_memory` | 自己的仓库，AstrBot 从这里更新 |
| `upstream` | `leafliber/astrbot_plugin_iris_memory` | 作者仓库，只读（push 已禁用） |

| 分支 | 含义 |
| --- | --- |
| `master` | 发布分支：`upstream/master` + 本仓库的自有提交，AstrBot 插件源默认拉这个分支 |
| `my-fixes` | 与 `master` 相同的别名分支，仅为兼容旧配置保留 |
| `backup/merge-history` | 改造前的历史备份，不要在上面继续开发 |

### 首次克隆初始化

新克隆或换机器后执行一次：

```bash
git remote add upstream https://github.com/leafliber/astrbot_plugin_iris_memory.git
git remote set-url --push upstream DISABLED   # 防止误推到作者仓库
git config core.hooksPath .githooks           # 启用推送前守卫
```

`master` 的第一个父提交永远是某个 `upstream/master` 提交，自有功能都是它上面的普通提交。
升级上游时用 rebase 重放这些提交，因此**冲突只会出现在自有提交里**，不会整份文件被上游覆盖。

## 自有功能清单

| 功能 | 位置 | 守卫测试 |
| --- | --- | --- |
| 统一上下文预算（`token_budget_max_tokens` 约束当前输入与全部动态上下文） | `iris_memory/core/llm_request_hook.py` `_apply_request_budget` | `test_shared_request_budget_is_wired` |
| 图片描述回填（占位符缺失时用缓存补 `[图:描述]`） | `_build_image_map` / `_get_inline_image_desc` 接入 `_collect_l1_context` | `test_image_backfill_is_wired` |
| 本轮图片上下文（当前消息的图片描述注入本轮请求，限时等待、超时降级） | `_collect_current_turn_image_context` + `preprocess_llm_request` | `test_current_turn_image_context_is_wired` |
| 图片长期记忆（解析结果写入 L2，语义可检索） | `iris_memory/image/memory_bridge.py`，接入 related/all 两条解析路径 | `test_image_memory_is_wired` |
| Telegram 适配器（图片下载、消息 ID、引用解析） | `iris_memory/platform/telegram.py` + 工厂注册 + `prepare_images()` | `test_telegram_adapter_is_registered` / `test_platform_prepare_images_is_wired` |
| 图片记忆按会话隔离、精确去重、TTL、缓存命中补写 | `store_image_memory` 的 `session_id` 去重/`skip_dedup`/`expires_at`；缓存命中路径补写 | `test_image_memory_dedupe_and_retention` |
| Telegram 图片临时文件清理 | `_remove_prepared_image_file` | `test_telegram_temp_files_are_cleaned` |
| 最近图片工具（`get_recent_images`） | `iris_memory/tools/recent_images.py` + 注册表 | `test_recent_images_tool_registered` |
| 错误友好化严格匹配（不误伤正常回复，拦截时记录原文） | `iris_memory/extras/error_friendly.py` | `test_error_friendly_stays_strict` |
| 摘要前清理未解析图片占位符 | `Summarizer._clean_image_placeholders` | `test_summary_strips_image_placeholders` |
| 重复回复去重（相邻重复 Bot 回复、同事件相同响应） | `_dedupe_l1_messages`、`llm_response_hook.py` | `test_l1_dedupe_is_wired` / `test_response_dedupe_is_present` |
| 插件源指向本仓库 | `metadata.yaml` `repo` | `test_metadata_points_to_this_fork` |

新增自有功能时：

1. 在 `master` 上以独立提交实现（一个功能一个提交，便于 rebase 与回滚）；
2. 在 `tests/fork/test_fork_features.py` 里加一条对应的守卫断言；
3. 在本文档的功能表里补一行。

## 同步上游

推荐用脚本（任何 shell 都能运行，不依赖 PowerShell 执行策略）：

```bash
# 同步并验证，不推送
python scripts/sync_upstream.py

# 同步、验证并推送 master 与 my-fixes
python scripts/sync_upstream.py --push

# 额外跑完整测试（需要 AstrBot 源码在 ..\AstrBot-v4.29.4）
python scripts/sync_upstream.py --push --full-tests
```

Windows 下若 `python` 不在 PATH，用仓库虚拟环境里的解释器：

```powershell
& .\.venv\Scripts\python.exe scripts\sync_upstream.py --push
```

手工等价命令：

```bash
git fetch upstream --prune
git log --oneline HEAD..upstream/master   # 看上游新增了什么
git rebase upstream/master                # 重放自有提交（会重写哈希）
python -m pytest tests/fork -q            # 守卫
git push --force-with-lease origin master
git push --force-with-lease origin master:my-fixes
```

> 变基会重写自有提交的哈希，所以推送必须带 `--force-with-lease`：
> 它在远端仍是本地已知状态时才覆盖，不会误删别人的提交。

### 冲突处理原则

- 以「保留两侧功能」为目标，先读上游新实现，再把自己的逻辑接进去；
- **禁止**用 `git checkout --theirs <文件>` 或整份文件取上游的做法收尾；
- 冲突解决后必须让 `pytest tests/fork -q` 通过，再跑一次完整测试；
- 若上游已实现相同功能，先确认行为等价再删除自己的实现，并同步删掉对应守卫。

## 上线

1. 推送 `master` 后，打开 AstrBot 面板 → 插件 → Iris Memory → 更多 → 重新安装；
2. 提示「未检测到新版本」时选择「强制更新」，它会从本仓库重新克隆默认分支并重载；
3. 更新日志应出现新的 `[Unreleased]` 内容；日志里确认插件重载、调度任务注册正常。

## 自动守卫

- `tests/fork/test_fork_features.py`：纯文件级断言，不需要 AstrBot 运行环境；
- `.github/workflows/fork-guard.yml`：每次 push / PR 自动运行上述守卫，缺功能直接失败；
- `.githooks/pre-push`：本地推送前再跑一次守卫。首次克隆后需要执行一次：

  ```bash
  git config core.hooksPath .githooks
  ```
