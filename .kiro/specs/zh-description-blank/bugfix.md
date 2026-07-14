# Bugfix Requirements Document

## Introduction

企业微信通知的"新增模型"表格中，"简介"列本应显示模型的中文简介，但部分模型（如 `kwaipilot/kat-coder-air-v2.5`、`kwaipilot/kat-coder-pro-v2.5`）始终显示为空白 `-`。

经排查确认，这类模型的共同特征是：OpenRouter 没有提供该模型对应的 HuggingFace 仓库标识（`hugging_face_id` 为空），但模型的 OpenRouter ID 本身形如"组织/名称"（可以据此推断出对应的 HuggingFace 仓库路径），并且 HuggingFace 官方源或镜像源当前是可达的。在这种情况下，简介抓取逻辑本应使用推断出的仓库路径继续尝试从 HuggingFace 抓取 README 并提取中文简介，但实际执行时会在内部触发一个未被正确处理的异常，该异常被上层的通用异常兜底逻辑静默吞掉，最终导致该模型的简介被强制置空——即使其在 HuggingFace 上可能存在可用的 README 内容，也从未被真正尝试抓取过。

此 bug 只影响"OpenRouter 未提供 HuggingFace 仓库标识，但模型 ID 本身可解析出组织/名称"这一类模型；已有 HuggingFace 仓库标识的模型，以及模型 ID 本身无法解析出组织/名称的模型，均不受影响。

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN 模型没有关联的 HuggingFace 仓库标识（`hugging_face_id` 为空）AND 该模型的 OpenRouter ID 本身可以解析出"组织/名称"形式的仓库路径 AND HuggingFace 官方源或镜像源可达 THEN 系统在尝试继续抓取简介时内部抛出一个未处理的异常，而不是使用解析出的仓库路径去抓取

1.2 WHEN 上述异常被抛出 THEN 系统的通用错误处理逻辑会静默捕获该异常并记录一条不包含具体原因的调试日志，同时将该模型的简介结果强制标记为"无简介"

1.3 WHEN 该模型的中文简介与 OpenRouter 原始英文简介均为空 THEN 企业微信通知表格中该模型的"简介"列显示为 `-`，即使其在 HuggingFace 上可能存在包含有效简介的 README 内容，也从未被真正尝试抓取

### Expected Behavior (Correct)

2.1 WHEN 模型没有关联的 HuggingFace 仓库标识 AND 该模型的 OpenRouter ID 可以解析出"组织/名称"形式的仓库路径 AND HuggingFace 官方源或镜像源可达 THEN 系统 SHALL 使用解析出的仓库路径继续尝试抓取 HuggingFace README，不得抛出未处理的异常

2.2 WHEN 使用解析出的仓库路径成功抓取到 HuggingFace README 且其中包含符合中文占比要求的段落 THEN 系统 SHALL 返回该中文简介，而不是回退到空文本

2.3 WHEN 使用解析出的仓库路径抓取 HuggingFace README 失败（仓库不存在、网络异常、触发频率限制）或 README 中不含中文段落 THEN 系统 SHALL 按现有的兜底顺序（翻译英文简介 → 使用 OpenRouter 英文简介 → 空）继续处理，不得抛出未捕获的异常

### Unchanged Behavior (Regression Prevention)

3.1 WHEN 模型本身已经关联了 HuggingFace 仓库标识（`hugging_face_id` 非空）THEN 系统 SHALL CONTINUE TO 直接使用该仓库标识抓取 HuggingFace README，行为与修复前一致

3.2 WHEN 简介抓取被配置为跳过联网（HuggingFace 源探测结果为"不可达"/跳过）THEN 系统 SHALL CONTINUE TO 跳过任何 HuggingFace 网络请求，直接走翻译 / OpenRouter 英文简介兜底

3.3 WHEN 模型没有关联的 HuggingFace 仓库标识 AND 该模型的 OpenRouter ID 无法解析出"组织/名称"形式的仓库路径 THEN 系统 SHALL CONTINUE TO 直接走翻译 / OpenRouter 英文简介兜底，不尝试任何 HuggingFace 网络请求

3.4 WHEN HuggingFace README 抓取过程中触发频率限制 THEN 系统 SHALL CONTINUE TO 捕获该情况并回退到翻译 / OpenRouter 英文简介兜底，不影响其他模型的批量处理

3.5 WHEN 某模型的中文简介与英文简介最终均为空 THEN 通知表格的渲染逻辑 SHALL CONTINUE TO 将该单元格显示为 `-`
