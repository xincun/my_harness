---
name: evolve
description: 数据驱动优化 AI harness 与项目记忆：基于 task.py stats 真实步数/拦截遥测与 kb_check 体检，将反复命中的暗坑升级为自动检查或铁律，反向修剪高摩擦规则与过期知识。task.py finish 提示达到阈值或用户要求复盘时触发。
---

# /evolve

严格执行 [`.harness/workflows/evolve.md`](../../../.harness/workflows/evolve.md) 中的全部阶段：

- 只修改 harness 配置与知识文件，绝不触碰业务代码；做减法优先于做加法。
- 结束时给出清晰的 `git diff` 摘要供人审阅。
