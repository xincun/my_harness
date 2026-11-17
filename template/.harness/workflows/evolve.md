# 工作流：自我进化（数据驱动优化 Harness 与项目记忆）

触发条件：`task.py finish` 提示达到进化阈值（默认累计 10 个任务）、用户主动触发 `/evolve`，或连续出现 `partial` / `failed` / 高频门禁拦截。

**边界**：只修改 Harness 配置与文档（`AGENTS.md`、`CLAUDE.md`、`.harness/`、`.claude/`、`.codex/`、`.agents/`、`docs/`），**绝不触碰业务源码**；全部改动以 `git diff` 交由人审阅。

## 第 1 步 · 采集客观遥测信号
```bash
python3 .harness/scripts/task.py stats         # 任务分布、档位占比、平均工具步数、Stop 拦截次数、高频模块与摩擦点
python3 .harness/scripts/kb_check.py           # 断链 / 失效符号锚点 / 模块环形依赖 / 缺模块卡片 / 待升级的坑
python3 .harness/scripts/codegraph.py doctor   # 代码图谱与 Git hooks 健康度
```
同时阅读 `.harness/evolution/tasks.jsonl` 最近一轮记录及 `LOG.md` 中上一轮进化的预期假设。

## 第 2 步 · 对症诊断（做减法优先，无证据不加规则）

| 遥测信号 | 根因诊断 | 处方 |
|---|---|---|
| `pitfalls.md` 某条目 `命中 ≥ 2` | 纯文档提醒约束力不足 | 能机器查的直接写成 `ARCHITECTURE.md` `禁止依赖`、`patterns.md` `禁用正则` 或 `verify` 步骤；其余提炼进 `AGENTS.md` 第 6 节 |
| `task.py stats` 显示 `Stop 拦截` 偏高或 Tier S 步数膨胀 | 流程仪式过重或提示词含混 | **反向修剪（Pruning）**：精简造成误拦或废动作的工作流步骤，不做加法叠补丁 |
| 高频模块反复出现“定位慢”摩擦 | 缺模块导航或术语断层 | 补齐对应 `docs/knowledge/modules/<模块>.md` 与 `glossary.md` |
| `kb_check` 报模块级环形依赖或锚点失效 | 架构劣化或文档过期 | 核查图谱后更新锚点、删除过期噪音，或将真实架构问题记入 `pitfalls.md` |
| 某关卡频繁被 `gate.py skip` | `verify` 某步骤过慢或不稳定 | 与用户确认后将慢步骤拆为 `"optional": true`，保持主校验快速可靠 |
| 某条规则需要第 2 次打例外补丁 | 触犯补丁计数器（不可判定机制） | 删除该启发式规则，改用编译器/Linter/CodeGraph 确定性能力 |
| `AGENTS.md` 超过预算（>200 行，目标 ≤80 行） | 入口信噪比劣化 | 将领域细则下沉至 `docs/` 按需加载，保持入口纯粹 |

## 第 3 步 · 回归检验上一轮进化
对照 `.harness/evolution/LOG.md` 上一轮的“预期效果”与本轮 `task.py stats` 的实际摩擦/步数变化：**没有带来可观测改善的规则当场撤销**，防止规则只增不减。

## 第 4 步 · 落盘进化记录并体检
在 `.harness/evolution/LOG.md` 顶部追加本次信号、改动文件与下一轮检验指标，然后执行：
```bash
python3 .harness/scripts/kb_check.py
python3 .harness/scripts/task.py mark-evolved
```
最后输出清晰的 `git diff` 摘要交由用户审阅确认。
