# 工作流：新增功能 / 修改需求 / 重构

目标：用户只给一句话，产出**位置准确、风格一致、真实跑通、按需留痕**的改动。

---

## 阶段 0 · 复杂度分档（先定 S / M / L，严禁小事大做或大事裸奔）

先运行 `codegraph.py explore "<关键词>"`（或查 `docs/knowledge/INDEX.md`）快速摸底，立即判定档位并口头告知：

- **Tier S · 极速有界通道**（同时满足：预计改动 ≤2 个文件、无公共接口/跨模块依赖变更、需求无歧义）：
  1. 查同文件/同模块现有写法与 `pitfalls.md` 相关词；
  2. 运行 `python3 .harness/scripts/task.py start --tier S --summary "..." --accept "C1: <外部可验证的正向与防回归行为>"`；
  3. **直接跳到阶段 4 实现** → 补/跑测试 → 自审 `git diff` → 运行唯一一次 `task.py finish --type feature --covers "C1=..."` → 用 **3 行极简格式**汇报（做了什么 / 验证结果 / 风险）。
- **Tier M · 标准通道**（跨文件业务功能、接口扩展、常规重构）：执行下方完整阶段 1–6。
- **Tier L · 架构 / 多步大特性通道**（新增核心模块、改公共契约/状态机、或拆解出 >5 个子步骤）：
  - 执行完整阶段 1–6；在阶段 1 将子目标拆为 `C1..Cn` 里程碑，每完成一个里程碑立即运行 `python3 .harness/scripts/task.py check --covers "Ck=<证据>"` 持久化打卡（跨会话不丢进度）；独立子模块优先用 `git worktree` 派发子代理并行推进。

> **单向棘轮（One-way Ratchet）**：实施中途若 `codegraph.py impact` 发现跨模块影响超出预期，立即执行 `task.py add --tier M`（或 `L`）升档补齐审查，严禁反向降档。

---

## 阶段 1 · 收敛边界与确立验收契约（Tier M / L，严禁连环提问）

1. **锚定现有代码边界**：
   - 系统内已有类似实体或半成品链路 → 收敛为“沿现有链路串联扩展”，不另起炉灶。
   - 系统内完全无此概念 → 按最小可行闭环（MVP）收敛。
2. **用“四分法”提炼 3–4 条黑盒验收条目（输入 → 外部可观测结果）**：
   - `C1` **Happy Path（端到端正向链路）**：从外部真实入口（CLI、HTTP/API、UI 或顶层调用）可验证的预期结果，不拿内部孤立 Mock 单测充数。
   - `C2` **Boundary & Negative（边界与拒绝防护）**：非法/越界/非目标输入返回明确错误且零副作用（涉及白名单/选择性处理时必须含否定性断言）。
   - `C3` **Non-Regression（零回归）**：未触发新行为时既有流程 100% 兼容。
   - `C4` **Side-Effect（状态与资源闭环）**：持久化、外部资源或异常中断时的对称清理。
3. **默认决策与暴露脆弱点**：
   - 若用户提出的方向本身有结构缺陷或过度设计，动手前先一针见血指出并给更简替代方案。
   - 仅在“二选一会导致推倒重写且无法从代码推导”时问 **1 个问题**（带 `(Recommended)`）；其余按现有系统惯例默认决定并声明 `【默认假设】`，同时点明最不自信的 `【脆弱边界】`。
4. **锁入契约**：
   ```bash
   python3 .harness/scripts/task.py start --tier M --summary "..." --accept "C1:..." --accept "C2:..." --accept "C3:..."
   ```

## 阶段 2 · 定位与寻找黄金参照
1. 读 `docs/ARCHITECTURE.md` 及对应 `docs/knowledge/modules/<模块>.md`（用 `glossary.md` 对齐业务词与代码命名）。
2. 先查图谱再 grep：`codegraph.py explore "<关键词>"`、`codegraph.py query <符号>`；大范围检索派只读 `explorer` 子代理。
3. **找出最相似的现有实现作为参照**（说不出参照实现在哪，说明定位未完成），修改公共符号前运行 `codegraph.py impact <符号>` 列出全部受影响调用方。

## 阶段 3 · 极简方案与按需架构核对
- **先问存在性**：能否复用标准库/框架现成机制或通过删减旧特判实现？严禁自造锁、状态同步器或命令解析器（确需基础设施时须对照 ≥2 个成熟开源实现源码）。
- **按需挂载高阶准则**：仅当改动涉及**新模块分层、对象依赖注入、异步回调或跨进程/有状态实体回收**时，打开 [docs/guidelines/architecture_principles.md](../../docs/guidelines/architecture_principles.md) 核对：Core/Driver 隔离、构造器 DI、无重入单向流、确定性回调时序与平滑排空（Drain）。

## 阶段 4 · 实现
- 测试与实现同步推进，模仿参照实现的命名、错误处理与分层写法（遵循 `patterns.md`）。
- Tier L 每完成一个独立里程碑并跑通局部校验（`verify.py --affected`）后，运行 `task.py check --covers "Ck=<证据>"` 锁定阶段成果。

## 阶段 5 · 真实链路核验与红队自审
1. **局部与运行态核验**：
   ```bash
   python3 .harness/scripts/codegraph.py affected
   python3 .harness/scripts/verify.py --smoke   # 若配置了 kind=smoke 的运行态冒烟步骤
   ```
   对照 `C1` 在真实调用入口执行一次黑盒核验，杜绝“单测全绿但真实入口不通”。
2. **对抗式自审 `git diff`**（>5 文件或改公共契约时派 `reviewer` 子代理）：
   - 空值/越界/非目标实体输入在哪一行最先穿透？
   - 多步操作中途异常中断是否会留下半完成脏状态或泄漏句柄？
   - 测试是否真的覆盖了真实调用路径而非镜像 Mock？

## 阶段 6 · 知识准入与唯一单步收尾
1. 按 [learn.md](learn.md) 第 1–2 步判断是否有过准入门槛的发现需要写入 `docs/knowledge/`（大多数常规任务无需写，保持知识库高信噪比）。
2. **全流程唯一一次执行 `task.py finish`**（自动跑全量 `verify.py`、`kb_check.py` 架构与断链门禁，并记录真实步数与模块指标）：
   ```bash
   python3 .harness/scripts/task.py finish --type feature \
     --covers "C1=<真实核验输出>" --covers "C2=<边界测试>" --covers "C3=verify.py" \
     --friction "none 或真实阻碍" --learned "none 或更新的条目"
   ```

## 汇报模板（Tier M / L）
```markdown
## 完成：<需求一句话> [Tier M/L]
- **验收凭据**：逐条 C1..Cn ✅（附关键命令/测试证据）
- **核心改动**：`path#符号` — 说明（参照实现：`path#符号`）
- **脆弱边界与残余风险**：什么极限条件下需关注
- **知识沉淀**：更新的条目（或“无，常规扩展”）
```
