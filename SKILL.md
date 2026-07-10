---
name: openrouter-model-checker
description: >-
  检测 OpenRouter 平台新增的 LLM 模型，自动抓取中文介绍，检测下线/价格/上下文变化，
  通过企业微信 Webhook 推送三段式通知。
  当用户提到"openrouter"、"OpenRouter 新模型"、"check-openrouter-models"、
  "openrouter model checker"、"OpenRouter 模型列表"、"模型上新"、"中文介绍"时使用此 skill。
---

# OpenRouter 模型上新检测(增强版)

通过 OpenRouter `GET /api/v1/models` API 拉取模型列表，与本地状态文件增量对比，
**自动抓取 HuggingFace 中文介绍**，检测**新增 / 下线 / 变更**，并通过企业微信推送。

## 功能

- **增量检测**：对比 OpenRouter API 与 `known_models.json`，报告新增模型
- **中文介绍**：对新模型抓取 HuggingFace README 中文段落，回退英文 description
- **下线检测**：标记 `removed_at`，保留模型快照，不重复报告
- **变更检测**：价格(>10%)、上下文窗口、支持模态等字段级 diff，critical/major/minor 三级判定
- **三段式通知**：新增 + 下线 + 重要变更，智能分片避开 4000 字节限制
- **首次基线**：首次运行写入全部当前模型，不发送通知
- **企业微信通知**：推送 markdown_v2 表格到群聊
- **GitHub Actions 部署**：定时 cron(北京时间 08:00) + 手动触发，自动 commit 状态文件

## 前置条件

| 变量名 | 必需 | 说明 |
|--------|------|------|
| `OPENROUTER_API_KEY` | ❌ | OpenRouter API Key（公开 API 无需设置）|
| `OPENROUTER_API_URL` | ❌ | API 地址（默认: `https://openrouter.ai/api/v1`）|
| `WECHAT_WEBHOOK_KEY` | ❌ | 企业微信机器人 Webhook Key（不设置则跳过通知）|
| `HF_TOKEN` | ❌ | HuggingFace Token（强烈建议，提升抓取速率限制）|

## 执行指令

当用户触发此 skill 时，在项目根目录执行：

```bash
python3 scripts/check_openrouter_models.py --log-file
```

**常用参数：**

| 参数 | 说明 |
|------|------|
| `--verbose` | 输出 DEBUG 级别日志 |
| `--log-file` | 同时输出日志到 `logs/check_openrouter_models.log` |
| `--quiet` | 无变化时跳过通知 |
| `--dry-run` | 调试：不写状态文件、不发通知，变化打印到 stderr |
| `--refresh-zh` | 强制重新抓取所有模型的中文介绍 |
| `--no-lock` | 不使用文件锁（Windows / CI 单进程）|

## 部署（GitHub Actions）

`.github/workflows/check-models.yml` 提供定时 + 手动触发。需配置 Secrets：
`WECHAT_WEBHOOK_KEY` / `OPENROUTER_API_KEY` / `HF_TOKEN`，并在仓库
Settings → Actions → General → Workflow permissions 开启 Read and write。

macOS 本地部署见 `launchd/`（每日 08:00）。

## 输出解读

**退出码：**

- `0` = 全部成功
- `1` = 部分失败（如通知发送失败或状态文件写入失败）
- `2` = 致命错误（如 OpenRouter API 不可达）

每次运行生成精简报告 `logs/reports/<date>.json`。

## 错误排查

| 错误信息 | 退出码 | 原因 | 解决方案 |
|----------|--------|------|----------|
| `OpenRouter API 认证失败 (401)` | 2 | `OPENROUTER_API_KEY` 无效 | 检查 Key 或留空使用公开 API |
| `OpenRouter API 触发频率限制 (429)` | 2 | 请求过于频繁 | 等待 1-5 分钟后重试 |
| `OpenRouter API 连接超时` | 2 | 网络不可达 | 检查网络连接 |
| `HF 429` | - | 中文抓取限流 | 配置 `HF_TOKEN`；自动指数退避 |
| `企业微信通知发送失败` | 1 | Webhook Key 无效 | 重新添加机器人 |
