# 工作流：首次接入与冷启动（建立工程上下文与验证轨道）

目标：让后续每个会话打开项目就**懂全局架构、有代码图谱、有校准过的编译/单测与真实运行态冒烟轨道**。

## 第 0 步 · 定义接入任务与合并已有入口
```bash
python3 .harness/scripts/task.py start --tier M --summary "onboard harness" \
  --accept "C1: 代码图谱与 verify/smoke 校验轨道实跑校准就绪" \
  --accept "C2: ARCHITECTURE.md、核心模块卡片与 AGENTS.md 填写完备且 kb_check 零错误"
```
若存在 `AGENTS.harness.md` / `CLAUDE.harness.md`，将项目原有入口文件中的专属规则、命令和约定无损合并进 Harness 版本对应章节，替换原文件后删除 `*.harness.md`。

## 第 1 步 · 调查仓库（或 0→1 空白目录冷启动）
查看根目录清单（`package.json`、`pyproject.toml`、`go.mod`、`Cargo.toml`、`CMakeLists.txt` 等）与顶层目录。
- **若除 Harness 外尚无任何业务源码（0→1 新项目）**：
  1. 若用户已说明技术栈与一期目标则直接采用；未说明则只问 1 句话确认技术栈与目标，并运行 `python3 .harness/scripts/task.py pause "等待确认新项目技术栈与一期目标"` 挂起本轮。
  2. 生成**最小可运行骨架（Walking Skeleton）**：构建清单、`.gitignore`、分层目录（如 `core/` 与入口适配层）、以及**至少 1 个真实跑通的端到端冒烟测试**。

## 第 1.5 步 · 构建代码图谱（CodeGraph）
```bash
python3 .harness/scripts/codegraph.py setup
python3 .harness/scripts/codegraph.py init
python3 .harness/scripts/codegraph.py install-git-hooks
python3 .harness/scripts/codegraph.py doctor
```

## 第 2 步 · 实证调研并铺设双层验证轨道（`verify` + 运行态 `smoke`）
先询问用户是否启用完成校验关卡（推荐启用；若用户明确拒绝，执行 `gate.py off verify --reason "<用户原话>"` 并跳过本步）。

1. **收集构建与测试证据**：
   ```bash
   python3 .harness/scripts/verify.py --survey
   ```
   优先级：**CI 配置** > 构建脚本 / Makefile / CMakePresets > README / CONTRIBUTING > 自动检测兜底。
2. **设计候选步骤（写入 `.harness/.state/verify-candidates.json`）**：
   - **基础校验层**：编译、类型检查/Lint、快速单元测试（必填，可重复幂等执行）。
   - **运行态黑盒冒烟层（`"kind": "smoke"`，强烈推荐）**：至少配 1 条从外部真实入口验证程序能跑通的快速黑盒探针（如 CLI 实跑命令、幂等拉起测试服务并发 HTTP 健康探活、或 UI 冒烟脚本），可通过 `verify.py --smoke` 单独秒级触发。
   - 超慢的全量集成/Benchmark 标 `"optional": true`；严禁放入部署、推送、外网写操作或改全局环境的命令。
3. **实跑校准与落盘**：
   ```bash
   python3 .harness/scripts/verify.py --calibrate --candidates .harness/.state/verify-candidates.json
   ```
   区分命令写错与项目原有基线失败（基线失败记入 `pitfalls.md`，不得谎称全绿）；将校准后的步骤与建议 `timeout` 写入 `.harness/project.json` 的 `verify` 数组并请用户过目。

## 第 3 步 · 编写架构总览与可执行分层规则 `docs/ARCHITECTURE.md`
（若已有非模板架构文档则保留原文、只补缺失模块表与分层规则）：
- 用 `codegraph.py files --max-depth 2` 和 `explore` 核实真实调用流（大仓可派多个 `explorer` 子代理并行调研）；
- 填写核心数据流、模块职责表、状态所有权与信任边界；
- 将核心分层约束直接写为可执行规则（如 `- 禁止依赖: core -> api`），`kb_check.py` 将基于图谱自动阻断反向依赖并预警模块级环形依赖。

## 第 4 步 · 核心模块卡片与项目代码样板
- 为最核心的 3–8 个模块按 `.harness/templates/module.md` 创建 `docs/knowledge/modules/<模块>.md` 并登记到 `docs/knowledge/INDEX.md`；
- 提炼 2–3 个本项目真实黄金样板写入 `patterns.md`，业务术语映射写入 `glossary.md`。

## 第 5 步 · 填写 `AGENTS.md` 第 1 节并一键闭环
填写 `AGENTS.md` 第 1 节项目速览，最后执行唯一收尾命令（自动跑 `verify.py` 全量检查与 `kb_check.py` 体检）：
```bash
python3 .harness/scripts/task.py finish --type other --summary "onboard harness" \
  --outcome success --covers "C1=verify+codegraph calibrated" --covers "C2=kb_check clean" \
  --learned "ARCHITECTURE,modules/*,patterns"
```
（若用户关闭了校验或存在基线失败未满足全部验收，使用 `--outcome partial --friction "..."` 如实记录。）
