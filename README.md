# OpenRouter 模型上新检测(增强版)

自动监控 [OpenRouter](https://openrouter.ai) 平台上的 LLM 模型变化,**自动抓取中文介绍**,通过企业微信推送**三段式通知**(新增 / 下线 / 重要变更)。

相比原 `openrouter-model-checker` skill,增强点:

| 能力 | 原版 | 增强版 |
|------|------|--------|
| 检测新增模型 | ✅ | ✅ |
| **中文介绍** | ❌ | ✅ HuggingFace README 抓取 + 回退英文 |
| **下线检测** | ❌ | ✅ 标记 `removed_at`,不丢历史 |
| **变更检测**(价格/上下文/模态) | ❌ | ✅ critical/major/minor 三级判定 |
| **三段式通知** | ❌ | ✅ 新增+下线+变更,智能分片 |
| 企业微信推送 | ✅ | ✅(保留) |
| 部署 | macOS launchd | **GitHub Actions** + launchd 二选一 |

## 工作原理

```
GitHub Actions 每天 08:00(北京时间,UTC 0:00) 触发
  ↓
fetch GET https://openrouter.ai/api/v1/models (公开 API,无需 key)
  ↓
与 known_models.json 对比
  ├─ 新增模型 → 并发抓 HuggingFace 中文 README → 缓存 zh_description
  ├─ 下线模型 → 标记 removed_at(保留 data 快照)
  └─ 字段变更 → 价格/上下文/模态 diff → 判定 critical/major/minor
  ↓
三段式通知推送到企业微信(避开 4000 字节限制)
  ↓
commit known_models.json + logs/reports/<date>.json 回仓库
```

## 快速开始

### 本地运行

```bash
pip install -r requirements.txt
cp .env.example .env          # 填入 WECHAT_WEBHOOK_KEY / HF_TOKEN(可选)

# 首次基线(写 known_models.json,不推送)
python3 scripts/check_openrouter_models.py --verbose

# 日常检测
python3 scripts/check_openrouter_models.py --log-file
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--verbose` | DEBUG 级别日志 |
| `--log-file` | 同时写日志到 `logs/check_openrouter_models.log` |
| `--quiet` | 无变化时跳过通知 |
| `--dry-run` | 不写状态、不发通知,变化打印到 stderr |
| `--refresh-zh` | 强制重新抓取所有模型的中文介绍 |
| `--no-lock` | 不使用文件锁(Windows / CI 单进程) |

## 部署到 GitHub Actions

1. 把项目 push 到 GitHub 仓库
2. 仓库 **Settings → Secrets and variables → Actions** 添加:
   - `WECHAT_WEBHOOK_KEY`(企业微信群机器人 Webhook Key)— 可选
   - `OPENROUTER_API_KEY`(可选,公开 API 不需要)
   - `HF_TOKEN`(强烈建议,免费账号把速率限制从 500/5min 升到 1000/5min)
3. **Settings → Actions → General → Workflow permissions** 勾选
   "Read and write permissions"(用于 commit 状态文件回仓库)
4. 手动触发一次:`Actions → Check OpenRouter Models → Run workflow`
5. 之后每天 UTC 0:00 自动运行

> **限流提示**:首次基线会抓取全部模型的中文介绍(可能 300+ 次 HF 请求)。
> 默认 5 并发、约 1 req/s,约 6 分钟完成。配置 `HF_TOKEN` 可显著提升限额。

## 中文介绍抓取策略

对每个新模型:

1. 从 `model.id` 解析 `org/name`,优先用 `model.hugging_face_id`(更准)
2. `GET https://huggingface.co/{repo_id}/raw/main/README.md`
3. 剥离 YAML frontmatter,统计中文比例判定语言
4. 提取第一段含中文的描述(截断 300 字)
5. 命中中文 → 缓存;否则回退到 OpenRouter 英文 `description`

缓存到 `known_models.json` 的 `zh_description` / `zh_source` / `zh_fetched_at`,
超过 30 天自动刷新(`--refresh-zh` 强制刷新)。

## 变更重要性判定

| 字段 | 严重度 | 触发条件 |
|------|--------|----------|
| `context_length` | **critical** | 上下文窗口变化 |
| `architecture.modality` | **critical** | 支持的模态变化 |
| `pricing.*` | **major** | 价格浮动 >10% |
| `supported_parameters` | **major** | 推理/工具参数集合变化 |
| `name` / `description` | minor | 单字段不报,≥3 个 minor 才报 |
| `created` / `top_provider` 等 | ignore | 永不报 |

只有 **critical / major** 变更推送企微,minor 仅写入日志与 commit message。

## 目录结构

```
.
├── .github/workflows/        # GitHub Actions (cron + dispatch)
├── launchd/                  # macOS 本地部署(备选)
├── scripts/
│   ├── check_openrouter_models.py   # 入口(兼容原调用契约)
│   ├── openrouter_checker/          # 核心包
│   │   ├── api.py            # OpenRouter + HF HTTP 客户端
│   │   ├── hf_card.py        # HF README 抓取 + 中文提取
│   │   ├── diff.py           # 新增/下线/变更检测
│   │   ├── notify.py         # 三段式通知 + 分片
│   │   ├── storage.py        # 状态文件 + 文件锁
│   │   ├── formatting.py     # 中文格式化
│   │   ├── config.py         # 配置/路径
│   │   └── wechat.py         # 企微推送
│   ├── tools/                # backfill_zh / simulate_changes
│   └── tests/                # 单元测试
└── known_models.json         # 状态文件(纳入 git)
```

## 测试

```bash
pip install -r requirements.txt
python -m pytest scripts/tests/ -v
```

## 工具

- `scripts/tools/backfill_zh_descriptions.py` — 基线后批量补抓已有模型的中文介绍
- `scripts/tools/simulate_changes.py` — 测试用,在 `known_models.json` 制造新增/下线/变更场景

## 许可

MIT
