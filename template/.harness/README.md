# .harness

AI 编码代理的驾驭控制层，面向人与三端宿主（Claude Code / Codex / Antigravity）统一工作。

## 核心设计原则
- **硬底线不变，仪式按 S/M/L 弹性缩放**：日常局部小改动（Tier S）零废话直通，复杂改动与大特性（Tier M/L）严守契约与里程碑打卡，所有档位统一过 `task.py finish` 真凭据门禁。
- **做减法与借力底座**：优先复用 Git 原生能力（blob SHA-1 指纹、`git worktree` 并发隔离）、CodeGraph SQLite 图谱与宿主原生 `transcript.jsonl` 遥测，不在应用层自造锁与语法解析器。
- **补丁计数器（Rule of Two Patches）**：同一机制第 2 次因漏判/误判需追加特判时，停下做结构性替换而非打第 3 个局部补丁。
- **数据驱动进化**：自动采集每单任务的 `tool_calls`、`stop_blocks`、`verify_runs` 与高频模块，用真实摩擦数据驱动 `/evolve` 修剪或升级规则。

## 目录结构
- `workflows/`：标准工作流（`feature` / `bugfix` / `learn` / `evolve` / `onboard`）
- `scripts/`：极简标准库脚手架（`task.py` 唯一任务门禁、`verify.py` 统一校验与 `--smoke`、`kb_check.py` 知识与图谱分层体检、`codegraph.py`、`gate.py`、`hooks/`）
- `templates/`：知识条目模板（模块卡片、Bug、ADR）
- `evolution/`：`tasks.jsonl`（含真实轨迹指标的任务日志，随 Git 共享）与 `LOG.md`（进化日志）
- `project.json`：项目专属 `verify`（含 `smoke` 步骤）与门禁阈值配置
- `.state/`：本地工作树运行时状态（`task.json` 与最近 15 次校验日志，已 gitignore）
