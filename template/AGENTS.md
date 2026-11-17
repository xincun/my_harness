# AGENTS.md — 项目驾驭手册（所有 AI 编码代理的唯一入口）

> 本文件每次会话自动加载，严控在 80 行以内：只放**路由、档位、铁律与完成契约**。
> 领域细节在 `docs/` 按需读取。本文件由 `/evolve` 维护，变更记录在 `.harness/evolution/LOG.md`。

## 1. 项目速览

<!-- onboard 工作流填写本节；保持 8 行以内 -->
- **项目**：（一句话说明这是什么、给谁用）
- **技术栈**：（语言 / 框架 / 运行时 / 数据存储）
- **入口**：（主要入口文件或服务）
- **架构总览**：见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **校验门禁**：`python3 .harness/scripts/verify.py`（含 `--smoke` 运行态黑盒冒烟，配置在 `.harness/project.json`）
- **代码图谱**：`python3 .harness/scripts/codegraph.py <命令>`（符号 / 调用链 / 爆炸半径 / 受影响测试）

## 2. 路由与轻重分流（底线统一，仪式按复杂度弹性缩放）

| 用户意图 | 工作流 | 快捷方式 |
|---|---|---|
| 新增功能 / 改需求 / 重构 | [.harness/workflows/feature.md](.harness/workflows/feature.md) | `/feature` |
| 修 bug / 报错 / 行为异常 | [.harness/workflows/bugfix.md](.harness/workflows/bugfix.md) | `/fix` |
| 只问问题、解释代码 | 先查第 4 节知识地图与代码图谱，直接回答 | — |
| 任务收尾与经验沉淀 | [.harness/workflows/learn.md](.harness/workflows/learn.md) | `/learn` |
| 复盘摩擦、优化 harness | [.harness/workflows/evolve.md](.harness/workflows/evolve.md) | `/evolve` |
| 首次接入 / 0→1 冷启动 | [.harness/workflows/onboard.md](.harness/workflows/onboard.md) | `/onboard` |

如果 `docs/ARCHITECTURE.md` 仍含模板标记，先执行 onboard。动手改动前必须用 `task.py start --tier S|M|L` 确立任务档位（**单向棘轮**：中途发现影响超预期只能 `task.py add --tier M|L` 升档，严禁降档）：
- **Tier S（极速有界）**：≤2 文件、局部明确改动、无跨模块/公共接口影响 → 1 条验收直奔实现与 `finish`，免繁琐文档仪式。
- **Tier M（标准交付）**：跨文件功能或常规 Bug → 参照实现定位 / 外部复现探针先见红 → 四分法契约 → 实现 → 自审沉淀 → `finish`。
- **Tier L（架构 / 多步大特性）**：改公共契约、核心状态机或 >5 子任务 → 拆解里程碑，用 `task.py check --covers C1=...` 逐项打卡，多代理用 `git worktree` 物理隔离。

## 3. 核心铁律（Tripwires — 触发即停）

1. **先查图谱与参照实现再动手**：定位先跑 `codegraph.py explore|callers`，改公共符号前必跑 `impact`；必须找出同模块现有实现作风格参照，遵循 [patterns.md](docs/knowledge/patterns.md)。
2. **先问存在性，严控复杂度预算**：优先做减法与复用宿主/标准库/Git 原生能力；自研锁、状态机、语法解析器前必须证明标准工具不可行。
3. **修 Bug 必先外部探针见红（RED → GREEN）与消融验证**：分诊期自由只读排查；动业务源码前必须写出真实调用链复现探针并亲眼见红，修后注释核心改动确认探针重回红色。
4. **补丁计数器（Rule of Two Patches）**：同一机制第 2 次因漏判/误判需追加白名单或特判时，立即停下向用户报告设计缺陷并给替代方案，严禁打第 3 个局部补丁。
5. **按需加载高阶架构卡**：仅当涉及模块拆分、跨进程/异步回调或有状态资源回收时，读取 [architecture_principles.md](docs/guidelines/architecture_principles.md) 核对分层、DI、确定性回调与平滑排空（Drain）。
6. **边界红线**：不读 `.env*` / 密钥，不擅改 CI、部署配置与大版本依赖，不执行 `git push`（除非用户明确指示）。
7. **唯一单步闭环**：全流程只在自审与 [learn.md](.harness/workflows/learn.md) 准入判断**之后**执行一次 `python3 .harness/scripts/task.py finish`（仅用户明确要求时方可用 `gate.py skip|off` 跳过并声明“未经校验”）。

## 4. 知识地图（按需读取，勿一次全读）

| 想知道 | 去看 |
|---|---|
| 功能实现与调用影响 | `codegraph.py explore "<词>"` / `callers\|callees\|impact <符号>` / `affected` |
| 模块职责与分层禁令 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 及 `docs/knowledge/modules/<模块>.md`（见 [INDEX](docs/knowledge/INDEX.md)） |
| 历史暗坑 / 项目代码样板 / 术语 | [pitfalls.md](docs/knowledge/pitfalls.md) · [patterns.md](docs/knowledge/patterns.md) · [glossary.md](docs/knowledge/glossary.md) |
| 通用工程架构与生命周期准则 | [docs/guidelines/architecture_principles.md](docs/guidelines/architecture_principles.md) · `decisions/` · `bugs/` |

## 5. 完成定义（Definition of Done）

- [ ] 新增或更新的测试覆盖本次改动（涉及运行态交互时已过 `verify.py --smoke` 或黑盒调用）
- [ ] 已自审 `git diff`（无调试残留、无关改动、硬编码密钥）并完成 `learn.md` 准入判断
- [ ] `task.py finish` 退出码为 0（自动完成全量 `verify`、`kb_check` 架构/断链检查与 `tasks.jsonl` 记录）
- [ ] 按对应档位简洁汇报（Tier S 三行结论；Tier M/L 附验收证据、改动点与残余风险）

## 6. 项目专属规则

<!-- 由 /evolve 从命中 ≥2 次的教训中提炼，注明来源如 (pitfalls#P003, 2026-11-17) -->
- （暂无）
