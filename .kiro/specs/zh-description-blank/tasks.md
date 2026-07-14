# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - 推断仓库路径应被使用而不崩溃
  - **CRITICAL**: 本测试在未修复代码上必须 FAIL —— FAIL 才能证明 bug 确实存在
  - **DO NOT** 在测试失败时去"修好"测试或提前改代码
  - **NOTE**: 本测试本身编码了期望行为（Property 1），修复后重新运行同一个测试即可验证修复是否生效
  - **GOAL**: 直接调用 `get_zh_description`（绕开 `batch_fetch_cards` 的通用 `except Exception`），复现裸 `AssertionError`，反证/验证 Hypothesized Root Cause
  - **Scoped PBT Approach**（本 bug 是确定性的，项目未安装 hypothesis）：用 `pytest.mark.parametrize` 覆盖 design.md 中 `isBugCondition` 下的 4 个等价类，而非随机生成
  - 新建测试文件 `scripts/tests/test_hf_card_resolved_repo.py`
  - 用 `unittest.mock.MagicMock` 模拟 `session`，参数化以下 4 个用例（均满足 `isBugCondition`：`hf_repo_id` 为空/`None`，`model_id` 形如 `"kwaipilot/kat-coder-air-v2.5"` 可解析出 `org/name`，`source="official"`）：
    1. mock `session.get` 返回 200 + 含中文段落的 README 文本
    2. mock `session.get` 返回 200 + 纯英文 README 文本，`openrouter_description` 非空
    3. mock `session.get` 返回 404（模拟仓库不存在）
    4. mock `session.get` 返回 429 + `Retry-After` header
  - 每个用例调用 `get_zh_description(model_id, None, openrouter_description, session, source="official")`，断言不抛出异常（`pytest.raises` 反向验证：先确认修复前会抛 `AssertionError`）
  - 额外追加一个用例：把上述用例 1 包进 `batch_fetch_cards([...])` 调用，断言修复前 `results[model_id].source == "none"` 且 `results[model_id].text == ""`（复现 bug 被通用异常吞掉、级联成空卡片的现象）
  - 在**未修复代码**上运行 `python -m pytest scripts/tests/test_hf_card_resolved_repo.py -v`
  - **EXPECTED OUTCOME**: 前 4 个用例均抛出 `AssertionError()`（`str(e) == ""`，无消息），且崩溃发生在 `session.get` 被调用之前（可通过 `session.get.assert_not_called()` 佐证）；第 5 个用例（经 `batch_fetch_cards`）不抛异常但返回空卡片
  - 记录反例：四个用例的崩溃点完全一致、与 README 内容/404/429 无关 → 印证根因是 `resolved` 未回写而非抓取逻辑本身
  - 测试写完、跑过、失败已记录后,标记本任务完成
  - _Requirements: 1.1, 1.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Bug Condition 不成立时行为保持不变
  - **IMPORTANT**: 遵循 observation-first 方法论 —— 先在未修复代码上跑通下列用例、记录真实输出，再把这些观测值写进断言
  - 新建测试文件 `scripts/tests/test_hf_card_resolved_repo.py`（与任务 1 同文件，增加 preservation 相关测试函数）
  - 用 `pytest.mark.parametrize` 覆盖 design.md 中 `¬isBugCondition` 的 4 个等价类（均不满足 bug 条件，为 preservation 范围）：
    1. **已有 `hugging_face_id`**：`model_id="meta-llama/llama-3.3-70b-instruct"`，`hf_repo_id="meta-llama/Llama-3.3-70B-Instruct"`，`source="official"`，mock README 含中文段落 → 观察未修复代码直接返回 `HfCard(source="hf-readme", ...)`，内容记录下来
    2. **`model_id` 不可解析**（不含 `/`）：`model_id="standalone-model"`，`hf_repo_id=None`，`source="official"`，`openrouter_description="Some desc"` → 观察未修复代码在 `if not resolved:` 提前返回 `openrouter-description` 兜底，且 `session.get.assert_not_called()`
    3. **`source=None`（跳过联网）**：`hf_repo_id=None`，`model_id="org/name"`，`source=None`，`openrouter_description="Some desc"` → 观察未修复代码同样提前返回、不发请求
    4. **已有仓库路径 + 429 限流**：`hf_repo_id="openai/gpt-4o"`，`source="official"`，mock 429 → 观察未修复代码被 `except RateLimitError` 捕获并回退到 `openrouter-description`
  - 对每个用例，断言 `get_zh_description(...)` 返回的 `HfCard` 的 `text`/`source`/`language` 等于观测到的值（把观测值直接写成断言里的期望值，即"捕获当前真实行为"）
  - 额外追加：`notify.py` 的 `-` 渲染保留性用例 —— 构造 `zh_description=""` 且 `description=""` 的模型字典，调用 `build_summary_message`，观察未修复代码渲染出的单元格为 `-`，写断言锁定（对应 Requirement 3.5；此用例不依赖 `get_zh_description`，验证的是下游渲染，未修复代码本就正确，此测试用于防止后续改动破坏它）
  - 在**未修复代码**上运行 `python -m pytest scripts/tests/test_hf_card_resolved_repo.py -v`
  - **EXPECTED OUTCOME**: 全部用例 PASS（这些是修复前就应成立的基线行为，用于后续防回归）
  - 测试写完、跑过、全部通过后,标记本任务完成
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix for「无 hugging_face_id 但可推断仓库路径时抛出未处理异常导致简介为空」

  - [x] 3.1 Implement the fix
    - 在 `scripts/openrouter_checker/hf_card.py` 的 `get_zh_description` 函数中,在 `if not resolved:` 与 `if source is None:` 两个提前返回之后,新增一行 `hf_repo_id = resolved`,把推断出的仓库路径回写到 `hf_repo_id`
    - 不修改 `resolved` 的计算逻辑、不修改后续的 `assert hf_repo_id is not None`、`fetch_hf_readme` 调用、`extract_zh_description` 调用、429 处理、兜底链顺序
    - 不修改 `batch_fetch_cards` 的通用 `except Exception` 兜底(继续作为真正意外错误的安全网)、不修改 `notify.py`
    - _Bug_Condition: isBugCondition(X) — X.hf_repo_id 为空 AND parse_openrouter_id(X.model_id) 非空 AND X.source 非 None(design.md Bug Details)_
    - _Expected_Behavior: Property 1 — 使用推断出的仓库路径继续抓取,不抛未处理异常,按现有兜底链继续(design.md Correctness Properties)_
    - _Preservation: Property 2 — hf_repo_id 本就非空、model_id 不可解析、source 为 None、429 限流兜底、notify 的 "-" 渲染均保持不变(design.md Correctness Properties)_
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - 推断仓库路径应被使用而不崩溃
    - **IMPORTANT**: 重新运行任务 1 里写的**同一个**测试文件 `scripts/tests/test_hf_card_resolved_repo.py` 中的 Property 1 用例,不要新写测试
    - 运行 `python -m pytest scripts/tests/test_hf_card_resolved_repo.py -v -k "not preserv"`(或直接跑整个文件观察对应用例)
    - **EXPECTED OUTCOME**: 之前 4 个会抛 `AssertionError` 的用例现在全部 PASS(不再抛异常),且各自落入正确的 `HfCard.source`(`hf-readme` / `hf-readme-no-zh` 或 `llm-translate`/`openrouter-description` / 同样走 404 兜底 / 同样走 429 兜底);经 `batch_fetch_cards` 的用例现在返回真实抓取结果而非空卡片
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Bug Condition 不成立时行为保持不变
    - **IMPORTANT**: 重新运行任务 2 里写的**同一批**测试,不要新写测试
    - 运行 `python -m pytest scripts/tests/test_hf_card_resolved_repo.py -v`(全文件)以及既有回归套件 `python -m pytest scripts/tests/ -v`
    - **EXPECTED OUTCOME**: 任务 2 的 4 个 preservation 用例 + notify "-" 渲染用例全部保持 PASS;`scripts/tests/test_hf_card.py`、`scripts/tests/test_api.py`、`scripts/tests/test_notify.py` 中的既有测试全部保持 PASS(无回归)
    - 确认修复后所有测试仍通过,如有异常向用户提出

  - [x] 3.4 补充集成测试:混合批次与通知渲染
    - **GOAL**: 验证修复在更贴近真实调用路径上的表现,覆盖 design.md Testing Strategy 中的 Integration Tests
    - 在 `batch_fetch_cards` 层面新增一个混合批次用例:`items` 中同时包含一个满足 bug 条件的模型(如 `kwaipilot/kat-coder-air-v2.5`,`hf_repo_id=None`)和一个不满足的模型(如已有 `hugging_face_id` 的模型),断言修复后前者不再落入 `source="none"`、能拿到真实抓取结果,后者结果与修复前一致,且前者从崩溃变为成功不影响后者的处理(现有逐模型隔离机制不变)
    - 在 `scripts/tests/test_notify.py` 补一个用例:构造一个此前受 bug 影响、且 mock README 含中文段落的模型,验证 `zh_description` 被正确填充后,`build_summary_message` 渲染出真实中文简介而不是 `-`(与任务 2 中"两者皆空→`-`"的用例互补)
    - _Requirements: 2.1, 2.2, 3.5_

- [x] 4. Checkpoint - Ensure all tests pass
  - 运行 `python -m pytest scripts/tests/ -v`,确认全部测试(含新增的 exploration/preservation/integration 测试与既有回归测试)通过
  - 如对任何失败或设计假设有疑问,向用户提出后再继续
  - **结果**: 46 passed, 1 failed。失败项 `test_formatting.py::test_format_price` 与本次修复无关(改动只涉及 `hf_card.py`,`formatting.py` 未被触及);已用 `git stash` 验证该用例在修复前同样失败,属于既有缺陷,不在本 bugfix 范围内,已告知用户。
