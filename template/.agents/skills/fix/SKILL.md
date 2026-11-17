---
name: fix
description: 修 bug 时使用：报错、异常、测试失败、行为不符合预期。自由只读分诊后，先写外部复现探针确认见红（RED），定位根因并做消融验证后转绿（GREEN），禁止压症状或打第 3 个局部补丁。
---

# /fix

严格执行 [`.harness/workflows/bugfix.md`](../../../.harness/workflows/bugfix.md) 中的阶段 0–4：

- 先查 `docs/knowledge/pitfalls.md` 与 `bugs/`；动源码前必须先跑出外部复现探针失败现场（RED）。
- 全流程只在最后执行唯一一次 `task.py finish`。
