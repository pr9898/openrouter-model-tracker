# zh-description-blank Bugfix Design

## Overview

当模型没有 `hugging_face_id`、但其 OpenRouter ID 本身能解析出 `组织/名称` 形式的仓库路径、且 HuggingFace 源可达时，`get_zh_description`（`scripts/openrouter_checker/hf_card.py`）本应改用推断出的仓库路径继续抓取 README。实际代码计算出了这个推断路径（局部变量 `resolved`），但从未把它赋回 `hf_repo_id`，导致函数随后对着仍为空的原始 `hf_repo_id` 执行 `assert hf_repo_id is not None`，抛出一个无消息的裸 `AssertionError`。这个异常在 `batch_fetch_cards` 的通用 `except Exception` 兜底里被静默吞掉，只留下一条无实际信息的 DEBUG 日志，该模型的简介被强制置为空卡片（`HfCard(text="", source="none")`）。如果模型的 OpenRouter 英文简介也为空，最终企业微信通知表格里就显示成 `-`。

修复方案是最小化的：在计算出 `resolved`之后，把它赋回 `hf_repo_id`，让函数后续的 `assert` 与 `fetch_hf_readme` 调用使用推断出的仓库路径。不改变已有的兜底链（翻译 → OpenRouter 英文简介 → 空）、不改变 429 限流处理、不改变 `notify.py` 的渲染逻辑。已通过直接调用 `get_zh_description` 复现：见下方 Bug Details。

## Glossary

- **Bug_Condition (C)**：触发 bug 的条件——模型没有 `hugging_face_id`（为空/`None`），且其 `model_id` 可解析出 `组织/名称` 路径，且 HuggingFace 源（官方或镜像）可达。
- **Property (P)**：`isBugCondition` 为真时的期望行为——使用推断出的仓库路径继续抓取，不抛出未处理异常，成功命中中文段落则返回中文简介，否则按现有兜底链继续。
- **Preservation**：`isBugCondition` 为假时必须保持不变的行为——已有 `hugging_face_id` 的模型、`model_id` 无法解析出仓库路径的模型、跳过联网的场景，以及限流兜底、`-` 渲染等既有逻辑。
- **`get_zh_description`**：`scripts/openrouter_checker/hf_card.py` 中的主入口函数，负责抓取并提取模型的中文简介，带多级 fallback。
- **`hf_repo_id`**：函数参数，来自 OpenRouter 模型对象的 `hugging_face_id` 字段，可能为 `None` 或空字符串。
- **`resolved`**：`get_zh_description` 内部的局部变量，表示应当用于抓取的仓库路径——优先取已有的 `hf_repo_id`，否则用 `parse_openrouter_id(model_id)` 推断出的 `组织/名称`。这是本次 bug 的核心：`resolved` 从未被赋回 `hf_repo_id`。
- **`parse_openrouter_id`**：辅助函数，将形如 `"org/name"` 的 `model_id` 拆分为 `("org", "name")`；`model_id` 中没有 `/` 时返回 `None`。
- **`source`**：HuggingFace 连通性探测结果，取值 `"official"` / `"hf-mirror"` / `None`（`None` 表示两个源都不可达，跳过联网）。
- **`HfCard`**：`get_zh_description` 的返回类型，字段为 `text`、`source`、`language`、`fetched_at`。
- **`batch_fetch_cards`**：并发调用 `get_zh_description` 的上层封装，内含通用 `except Exception`，是本 bug 中异常被静默吞掉的位置。

## Bug Details

### Bug Condition

Bug 出现在 `get_zh_description` 内部：当 `hf_repo_id` 为空（`None` 或空字符串）时，函数进入 `if source is None or not hf_repo_id:` 分支，计算出 `resolved`（已有 id 或从 `model_id` 推断出的 `org/name`）。若 `resolved` 非空且 `source` 非 `None`（即模型 ID 可解析、且网络可达），代码本应继续使用 `resolved` 去抓取 README，但由于 `resolved` 未被赋回 `hf_repo_id`，控制流会落到函数末尾的 `assert hf_repo_id is not None`——此时 `hf_repo_id` 仍是最初传入的空值，断言失败，抛出裸 `AssertionError()`。

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X of type ZhDescriptionInput
    X.model_id:   string          // OpenRouter 模型 ID，如 "kwaipilot/kat-coder-air-v2.5"
    X.hf_repo_id: string OR null  // 来自 OpenRouter 的 hugging_face_id 字段，可能为 null 或 ""
    X.source:     string OR null  // HF 连通性探测结果："official" | "hf-mirror" | null(跳过联网)
  OUTPUT: boolean

  repo_from_id ← parse_openrouter_id(X.model_id)         // 无 "/" 时为 null
  resolved     ← is_empty(X.hf_repo_id)
                   ? (repo_from_id != null ? join(repo_from_id, "/") : null)
                   : X.hf_repo_id

  RETURN  is_empty(X.hf_repo_id)         // OpenRouter 未提供仓库标识
          AND resolved != null            // 但 model_id 本身可解析出 org/name
          AND X.source != null            // 且 HF 官方源或镜像源可达
END FUNCTION
```

### Examples

- **示例 1（复现，已验证）**：`get_zh_description("kwaipilot/kat-coder-air-v2.5", None, "", session, source="official")`
  - **实际（未修复）**：函数内部 `assert hf_repo_id is not None` 失败，抛出 `AssertionError()`（`str(e) == ""`，无任何消息）。直接调用可复现；经 `batch_fetch_cards` 调用时，该异常被其 `except Exception as e:` 捕获，记录 `logger.debug("[hf_card] %s 抓取失败: %s", model_id, e)`（`%s` 部分为空），结果写为 `HfCard(text="", source="none", ...)`。
  - **期望**：使用推断出的仓库路径 `kwaipilot/kat-coder-air-v2.5` 请求 `https://huggingface.co/kwaipilot/kat-coder-air-v2.5/raw/main/README.md`，若命中中文段落则返回该中文简介。
- **示例 2**：`kwaipilot/kat-coder-pro-v2.5`，与示例 1 同理，同样的崩溃路径。
- **边界示例（同属 bug 条件）**：`hf_repo_id=""`（空字符串而非 `None`）触发同样的崩溃——Python 中 `not ""` 为真，与 `hf_repo_id is None` 走向同一分支。
- **边界示例（不属于 bug 条件，用于对照）**：`model_id="standalone-model"`（不含 `/`）、`hf_repo_id=None`、`source="official"` — `parse_openrouter_id` 返回 `None`，`resolved` 为 `None`，函数在 `if not resolved:` 处提前 return，根本不会到达崩溃点，此输入不触发 bug。

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- 已有 `hugging_face_id` 的模型：继续直接用该 id 抓取 README，完全不经过本次修改的分支。
- `model_id` 无法解析出 `org/name`（不含 `/`）的模型：继续在 `if not resolved:` 处提前返回翻译/英文兜底，不发起任何 HF 网络请求。
- `source=None`（HF 官方与镜像均不可达）：继续跳过联网，直接走翻译/OpenRouter 英文简介兜底。
- HuggingFace README 抓取触发 429 限流：继续被 `except RateLimitError` 捕获并回退，不影响批次中其他模型的处理（`batch_fetch_cards` 逐模型隔离失败）。
- 中文简介与英文简介最终均为空的模型：通知表格渲染逻辑（`notify.py`）继续显示 `-`。

**Scope:**
所有不满足 Bug Condition（即 `hf_repo_id` 非空，或 `model_id` 无法解析出 `org/name`，或 `source` 为 `None`）的输入都不受本次修复影响，包括：
- 已关联 HuggingFace 仓库的模型（如 `meta-llama/llama-3.3-70b-instruct`）
- ID 中不含 `/` 的模型
- HF 源整体不可达的批次运行
- 抓取过程中的限流场景（无论是否走推断路径）

## Hypothesized Root Cause

以下根因已通过直接调用 `get_zh_description` 复现确认（见 Bug Details 示例 1），并排除了其他可能性：

1. **局部变量未回写（已确认根因）**：`get_zh_description` 中，`resolved = hf_repo_id or ("/".join(repo) if repo else None)` 只是计算出一个新值，从未执行 `hf_repo_id = resolved`。函数末尾 `assert hf_repo_id is not None` 检查的是原始参数 `hf_repo_id`，而不是刚推断出的 `resolved`，所以当原始 `hf_repo_id` 为空、但 `resolved` 有效时，断言必然失败。

2. **通用异常处理掩盖了崩溃**：`batch_fetch_cards` 的 `_worker` 没有单独 try/except，异常经 `future.result()` 抛出后被外层 `except Exception as e:` 捕获，记录 `logger.debug(..., e)`。由于 `AssertionError()` 没有携带任何消息，`str(e)` 为空串，日志里看不到任何可定位问题的信息——这正是 bug 长期未被察觉的原因。

3. **级联到空文案**：捕获后该模型被写为 `HfCard(text="", source="none")`；`notify.py` 的 `_model_zh` 在 `zh_description` 为空时回退到 OpenRouter 英文 `description`；报告中的两个问题模型英文简介同样为空，因此最终表格显示 `-`。

**已排除的其他假设：**
- 不是网络/DNS 问题：`check_hf_reachable` 已在调用 `batch_fetch_cards` 前确认源可达，且崩溃发生在任何 HTTP 请求发出之前。
- 不是 `extract_zh_description` 的中文占比判定问题：该函数从未被调用到，崩溃点在 `fetch_hf_readme` 调用之前。
- 不是限流（429）处理问题：`RateLimitError` 分支代码本身没有问题，只是从未被执行到。

## Correctness Properties

Property 1: Bug Condition - 推断仓库路径应被使用而不崩溃

_For any_ input where the bug condition holds（`isBugCondition` 为真：`hf_repo_id` 为空，`model_id` 可解析出 `org/name`，且 HF 源可达），the fixed `get_zh_description` function SHALL 使用推断出的仓库路径发起 README 抓取，不得抛出未处理异常；若抓取到的 README 含符合中文占比要求的段落，SHALL 返回该中文简介（`source="hf-readme"`）；若抓取失败（仓库不存在、网络异常、触发 429 限流）或 README 无符合要求的中文段落，SHALL 按现有兜底顺序（`hf-readme-no-zh` → `llm-translate` → `openrouter-description` → `none`）继续处理，不得抛出未捕获异常。

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Bug Condition 不成立时行为保持不变

_For any_ input where the bug condition does NOT hold（`hf_repo_id` 本就非空，或 `model_id` 无法解析出 `org/name`，或 `source` 为 `None`），the fixed `get_zh_description` function SHALL 产出与修复前完全相同的 `HfCard`（`text`、`source`、`language` 均一致），包括继续正确捕获已有仓库路径抓取时的 429 限流并回退；同时通知表格渲染逻辑 SHALL 在中英文简介均为空时继续显示 `-`。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

**File**: `scripts/openrouter_checker/hf_card.py`

**Function**: `get_zh_description`

**Specific Changes**:

1. **回写 `resolved` 到 `hf_repo_id`**：在 `if source is None or not hf_repo_id:` 分支内，`if not resolved: ...` 与 `if source is None: ...` 两个提前返回之后，新增一行 `hf_repo_id = resolved`，让函数末尾的 `assert` 与后续的 `fetch_hf_readme(session, hf_repo_id, ...)` 调用使用推断出的仓库路径，而不是原始的空值。

   ```python
   if source is None or not hf_repo_id:
       repo = parse_openrouter_id(model_id)
       resolved = hf_repo_id or ("/".join(repo) if repo else None)
       if not resolved:
           return _try_translate("openrouter-description", "en")
       if source is None:
           return _try_translate("openrouter-description", "en")
       hf_repo_id = resolved  # 新增:回写推断出的仓库路径，供后续 fetch 使用
   ```

2. **不改动兜底链、429 处理、`extract_zh_description`**：这些逻辑在 `hf_repo_id` 被正确赋值后本就是对的，修复只是让它们对推断路径同样生效，不需要重写。

3. **不改动 `notify.py` / `batch_fetch_cards`**：`batch_fetch_cards` 里的通用 `except Exception` 保留作为兜底安全网（针对真正意外的错误）；一旦这类输入不再崩溃，该分支自然不会再被它触发。`notify.py` 的 `-` 渲染逻辑（Unchanged Behavior 3.5）完全不在改动范围内。

4. **保留末尾的 `assert hf_repo_id is not None`**：修复后，这个断言在所有能到达它的路径上都必然为真（由前面两个 `if not resolved` / `if source is None` 提前返回保证），从「误报」变为「真正的不变式」，继续作为防御性文档保留，不需要删除或改写。

## Testing Strategy

### Validation Approach

采用两阶段策略：先在未修复代码上写探索性测试，复现并确认崩溃点；再编写保留性测试锁定不受影响的行为；最后实施修复并验证两类测试都通过。

**环境约束说明**：项目当前未安装 `hypothesis`，且本 bug 是确定性的（不依赖随机采样即可稳定复现——只要 `hf_repo_id` 为空、`model_id` 可解析、`source` 可达就必然触发）。因此下文所有「Property-Based Tests」均通过纯 `pytest`（`@pytest.mark.parametrize`）实现为**确定性的 scoped 属性测试**：每个用例对应 `isBugCondition` 谓词下的一个等价类，而不是随机生成输入。这与工作流建议的「Scoped PBT Approach」（面向确定性 bug，将属性收敛到具体失败用例）一致。

### Exploratory Bug Condition Checking

**Goal**：在实施修复前，直接调用 `get_zh_description`（绕过 `batch_fetch_cards` 的吞异常逻辑）复现裸 `AssertionError`，确认根因分析。

**Test Plan**：用 `unittest.mock` 模拟 `session.get`，对四个覆盖 `isBugCondition` 不同分支的等价类分别调用 `get_zh_description`，在未修复代码上运行并观察失败。

**Test Cases**（均满足 `hf_repo_id` 为空 + `model_id` 可解析 + `source` 可达）：
1. **README 含中文段落**：`model_id="kwaipilot/kat-coder-air-v2.5"`，mock 返回含中文段落的 README（会失败于修复前代码）
2. **README 无中文段落**：`model_id="kwaipilot/kat-coder-pro-v2.5"`，mock 返回纯英文 README，`openrouter_description` 非空（会失败于修复前代码）
3. **README 抓取 404**：mock 返回 404（`fetch_hf_readme` 返回 `None`），`openrouter_description` 非空（会失败于修复前代码）
4. **README 抓取触发 429**：mock 返回 429 + `Retry-After`（会失败于修复前代码）

**Expected Counterexamples**：
- 四个用例在未修复代码上**全部**抛出同一种裸 `AssertionError()`（`str(e) == ""`），且崩溃发生在任何 `session.get` 调用之前——说明崩溃点在进入抓取逻辑之前，与 README 内容、404、429 无关，直接印证根因是 `resolved` 未回写，而不是抓取/解析逻辑本身的问题。
- 经 `batch_fetch_cards` 间接调用时，日志中 `抓取失败: ` 后为空，`results[model_id]` 变为 `HfCard(text="", source="none")`。

### Fix Checking

**Pseudocode:**
```
FOR ALL X WHERE isBugCondition(X) DO
  result := get_zh_description'(X.model_id, X.hf_repo_id, desc, session, source=X.source)
  ASSERT no_exception_raised(result)
  ASSERT fetch_was_attempted_against(resolved_repo_path(X))
  ASSERT (has_zh_paragraph(readme) => result.source == "hf-readme" AND result.text == extracted_zh)
  ASSERT (NOT has_zh_paragraph(readme) OR fetch_failed => result.source IN
          {"hf-readme-no-zh", "llm-translate", "openrouter-description", "none"})
END FOR
```

### Preservation Checking

**Pseudocode:**
```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT get_zh_description(X) = get_zh_description'(X)   // F(X) 与 F'(X) 结果完全一致
END FOR
```

**Testing Approach**：由于本项目未安装 `hypothesis`，preservation 测试通过 `pytest.mark.parametrize` 覆盖 `¬isBugCondition` 的每个等价类（而非随机采样），仍能对「所有非 bug 输入结果不变」给出确定性保证，因为这些等价类穷尽了 `¬isBugCondition` 在代码路径层面的分支组合。

**Test Plan**：先在未修复代码上运行以下用例，记录实际输出，再断言修复后输出完全相同。

**Test Cases**（均满足 `¬isBugCondition`）：
1. **已有 `hugging_face_id`**：`model_id="meta-llama/llama-3.3-70b-instruct"`，`hf_repo_id="meta-llama/Llama-3.3-70B-Instruct"`，`source="official"`，README 含中文段落 → 修复前后均直接返回 `hf-readme` 卡片，内容一致（此分支完全不经过被修改的代码）
2. **`model_id` 不可解析**：`model_id="standalone-model"`（无 `/`），`hf_repo_id=None`，`source="official"`，`openrouter_description="Some desc"` → 修复前后均在 `if not resolved:` 提前返回 `openrouter-description` 兜底，内容一致
3. **`source=None`（跳过联网）**：`hf_repo_id=None`，`model_id="org/name"`，`source=None`，`openrouter_description="Some desc"` → 修复前后均在 `if source is None:` 提前返回，`session.get` 从未被调用
4. **已有仓库路径 + 429 限流**：`hf_repo_id="openai/gpt-4o"`，`source="official"`，mock 429 → 修复前后均被 `except RateLimitError` 捕获并回退到 `openrouter-description`，此分支完全不经过被修改的代码

### Unit Tests

- `get_zh_description` 在 `hf_repo_id` 为空、`model_id` 可解析、`source` 可达时不抛出异常（直接调用，隔离 `batch_fetch_cards` 的吞异常层，确保测的是修复本身而不是兜底层）
- 保留现有 `parse_openrouter_id`、`extract_zh_description`、`detect_language` 单测不变（`scripts/tests/test_hf_card.py`），确认修复未触及这些函数
- 保留现有 `fetch_hf_readme`、`check_hf_reachable` 单测不变（`scripts/tests/test_api.py`）

### Property-Based Tests

（均为确定性 scoped 参数化测试，详见 Exploratory / Preservation Checking 中的 Test Cases）

- **Property 1 参数化集合**（4 个等价类）：验证 bug 条件成立时，四种抓取结果（命中中文/无中文/404/429）都不再崩溃，且分别落入正确的 `HfCard.source`
- **Property 2 参数化集合**（4 个等价类）：验证 bug 条件不成立时，四种既有路径（已有仓库/不可解析/跳过联网/已有仓库+限流）修复前后结果完全一致

### Integration Tests

- `batch_fetch_cards` 混合批次：同一批 `items` 中既包含满足 bug 条件的模型（如 `kwaipilot/kat-coder-air-v2.5`），也包含不满足的模型（如已有 `hugging_face_id` 的模型）；验证修复后前者不再被通用 `except Exception` 吞掉、能拿到真实抓取结果，后者结果与修复前一致，且前者的失败不会影响后者（现有的逐模型隔离机制不变）
- `notify.py` 渲染回归：验证 Requirement 3.5——构造一个 `zh_description` 与 `description` 均为空的模型，`build_summary_message` 仍渲染 `-`；同时验证一个此前受 bug 影响、修复后应有真实中文简介的模型不再显示 `-`
