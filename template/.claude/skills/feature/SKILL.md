---
name: feature
description: 新增功能、修改需求、改行为或重构时使用。按改动复杂度自动分档（Tier S 极速有界 / Tier M 标准交付 / Tier L 架构与多里程碑），定位黄金参照实现，单步验证闭环。
---

# /feature

按 [`.harness/workflows/feature.md`](../../../.harness/workflows/feature.md) 先定档（S/M/L）再执行对应阶段：

- Tier S（≤2 文件、局部明确改动）直奔实现与 `task.py finish`；Tier M/L 严格执行契约、参照定位、自审与 `learn` 后唯一一次 `task.py finish`。
- 大范围定位派 `explorer` 子代理；Tier M（>5 文件）或 Tier L 收尾前派 `reviewer` 子代理。

用户的原始请求：$ARGUMENTS
