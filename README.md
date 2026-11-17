# AI Harness 工程母版（Workspace Generator）

给软件工程项目装上一层**可版本化、跨宿主通用、能自我进化的 AI 编码代理驾驭层（AI Harness）**。接入后，在 **Claude Code、Codex 或 Antigravity** 中打开项目，只说一句话需求或报障，Agent 即可按工业级工程标准完成交付：

1. **打开即懂全局（双层上下文）**：`SessionStart` 自动注入精简项目简报；结构事实交给 **CodeGraph（tree-sitter 本地 SQLite 图谱）** 实时查询符号、调用链与爆炸半径，隐性经验留在 `docs/knowledge/` 按需渐进加载。
2. **底线统一、仪式按 S / M / L 弹性缩放**：日常局部小改动走 **Tier S 极速通道**（零废话直通，体感无延迟）；常规跨文件需求与线上故障走 **Tier M 标准闭环**（参照实现 / 外部复现探针先见红 RED → 消融验证 → 转绿 GREEN）；架构重构与大特性走 **Tier L 里程碑通道**（`task.py check` 跨会话打卡 + `git worktree` 物理隔离并发），并由**单向棘轮**防止中途降档逃避审查。
3. **硬凭据完成门禁（杜绝嘴上“修好了”）**：验收契约与当前 **Git 工作树内容指纹（HEAD + Git blob SHA-1）** 强绑定；必须通过实跑校准的 `verify.py`（含 `--smoke` 运行态黑盒冒烟轨道）与 `kb_check.py`（断链、符号锚点、CodeGraph 图谱跨层依赖及模块环形依赖检查），`Stop` Hook 才会放行完成。
4. **数据驱动自我进化（零人工填报遥测）**：从三端宿主原生的 `transcript.jsonl` 自动采集每单任务的**工具调用步数（`tool_calls`）、门禁拦截次数（`stop_blocks`）、校验轮数（`verify_runs`）与高频模块**写入 `.harness/evolution/tasks.jsonl`；累计达阈值触发 `/evolve`，将反复命中的暗坑升级为自动检查或铁律，反向修剪高摩擦规则。

---

## 快速开始（先注册全局关键字 `/harness`，任意项目一句话接入）

在本母版仓库执行一次全局关键字注册（同时写入 Antigravity、Claude Code、Codex 全局 Skills）：
```bash
python3 install.py --register-global
```
之后打开**任意其他项目**（存量仓库或空白新文件夹），直接输入 **`/harness`**（或说 *“接入 harness”* / *“`/harness link`”* / *“`/harness` 用 Go 写个短链服务”*），Agent 就会自动完成母版安装、初始化或无损同步升级。

---

## 命令行手动分发模式（支持 4 种拓扑）

### 模式 A · 团队独立入库（默认推荐，零外部依赖，适合 Git 团队共享与 CI）
```bash
python3 install.py /path/to/your-project
# 母版迭代后平滑升级控制平面（自动清理废弃文件，绝不覆盖项目专属 docs/ 与 project.json）：
python3 install.py /path/to/your-project --upgrade
```

### 模式 B · 本机多项目零拷贝联动（改一次母版，本机所有项目 0 秒同步生效）
```bash
python3 install.py /path/to/your-project --link
```
控制平面（脚本、工作流、Skills、通用架构准则）直接软链回本母版仓库，目标项目仅保留自身数据文件。

### 模式 C · 跨三端原生 Plugin + 极薄业务仓（零控制平面文件入库）
```bash
# 1. 导出多宿主 Plugin 包（含 Antigravity plugin.json / .claude-plugin / .codex-plugin 及工作区感知 Hooks）
python3 install.py --export-plugin ~/.gemini/config/plugins/ai-harness

# 2. 目标业务仓库仅写入 ≤6 个项目专属数据文件（AGENTS.md / project.json / docs/knowledge）
python3 install.py /path/to/your-project --thin
```
> 插件内置预启动工作区探测（`_boot.py`）：在未接入 `.harness/` 的普通目录中 3ms 静默放行，绝不干扰非受管项目。

### 模式 D · 0→1 空白目录冷启动
```bash
mkdir my-new-project && cd my-new-project && git init
python3 /path/to/install.py .
```
打开项目直接说：*“用 Python FastAPI 搭建一个订单服务骨架，并执行 onboard 接入 harness”* —— Agent 会自动完成最小可运行骨架、冒烟测试、代码图谱与架构文档初始化。

---

## 四层工程架构

```
用户一句话需求 / 报障
   │
   ▼
┌────────────────────── 1. 上下文层 ──────────────────────┐
│ SessionStart Hook → 项目简报（当前任务 / Git / 高频暗坑） │
│ AGENTS.md（≤80行内核）→ 意图路由 + S/M/L 分档 + 核心红线 │
│ CodeGraph 图谱 → 符号 / 调用链 / 爆炸半径 / 受影响测试   │
│ docs/ → 架构总览 + 按需加载模块卡片 / 样板 / 通用架构准则 │
└─────────────────────────────────────────────────────────┘
   │ 自动分档（单向棘轮 S → M → L）
   ▼
┌────────────────────── 2. 流程层 ────────────────────────┐
│ Tier S：局部明确改动 → 1 条验收直奔实现与 finish         │
│ Tier M：feature（四分法契约） / bugfix（先红后绿+消融）   │
│ Tier L：里程碑持续打卡（task.py check）+ git worktree    │
└─────────────────────────────────────────────────────────┘
   │
   ▼
┌────────────────────── 3. 凭据门禁层 ────────────────────┐
│ PreToolUse（guard.py）→ 保护控制平面与 .env/密钥（含补丁）│
│ verify.py → 编译/单测 + `--smoke` 真实运行态黑盒冒烟     │
│ kb_check.py → 断链 + path#符号锚点 + 图谱 DEPS/环依赖检查 │
│ task.py finish + Stop Hook → Git 指纹绑定，唯一单步闭环  │
└─────────────────────────────────────────────────────────┘
   │
   ▼
┌────────────────────── 4. 遥测进化层 ────────────────────┐
│ transcript.jsonl 自动采集 → 步数 / 拦截数 / 校验数 / 模块 │
│ learn → 四问准入过滤，高信噪比写回知识库或可执行规则      │
│ evolve → 命中≥2 升级自动门禁，高摩擦规则反向修剪并留痕    │
└─────────────────────────────────────────────────────────┘
```

---

## 核心组件速查（`template/`）

| 路径 | 职责 |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | 极简统一入口（≤80 行）：意图路由、S/M/L 分档、铁律与完成定义 |
| `.harness/workflows/` | `onboard` / `feature` / `bugfix` / `learn` / `evolve` 五套跨宿主工作流 |
| `.harness/scripts/task.py` | **唯一任务与完成门禁入口**：`start` / `add`（单向升档）/ `check`（里程碑打卡）/ `pause` / `finish` / `status` / `stats` / `mark-evolved` |
| `.harness/scripts/verify.py` | 统一验证器：`--survey` 取证、`--calibrate` 实跑校准计时、`--affected` 受影响测试、`--smoke` 运行态黑盒冒烟、`--parallel` 并行执行，自带日志自动轮转 |
| `.harness/scripts/kb_check.py` | 知识与可执行架构体检：断链、`path#符号` 锚点校验、基于 **CodeGraph SQLite `edges` + Python AST** 的分层 `DEPS` 阻断与模块环形依赖（`A ↔ B`）检测 |
| `.harness/scripts/kb_anchors.py` | 证据锚点工具：`--check` 检查失效符号，`--fix` 将行号引用自动改写为 `path#Symbol` |
| `.harness/scripts/codegraph.py` | CodeGraph CLI 统一封装：项目内隔离安装（`setup`）、初始化（`init`）、Git hooks 自动增量同步与结构化查询 |
| `.harness/scripts/gate.py` | 用户可控关卡开关：`status` / `on` / `off` / `skip`（必须附 `--reason` 留痕，不在免确认白名单内） |
| `.harness/scripts/hooks/` | `_boot.py`（3ms 工作区预探测）、`session_start.py`、`guard.py`、`stop_gate.py`，严格对齐 Claude Code / Codex / Antigravity 三端原生协议 |
| `docs/guidelines/architecture_principles.md` | 按需挂载的工业级架构准则：Core/Driver 隔离、构造器 DI、无重入单向流、确定性回调时序与平滑排空（Drain） |

---

## 开发与验证本母版

本母版脚本仅依赖 **Python 3.8+ 标准库**（无需额外 pip 依赖）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 evals/run.py --output /tmp/harness-eval.json
```

测试集覆盖：四种安装/升级/软链/Plugin 导出拓扑、三端宿主 Hook 真实契约金丝雀（Claude / Codex `apply_patch` / Antigravity `continuationActive`）、Git 指纹防篡改、CodeGraph 真实 CLI 与图谱分层/环检测、真实 `git worktree` 并发交接及会话轨迹遥测统计。
