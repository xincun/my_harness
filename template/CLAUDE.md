@AGENTS.md

## Claude Code 专属说明

- **自动技能路由**：`/feature` `/fix` `/learn` `/evolve` `/onboard` 已封装为 Skills；用户只说一句话需求时，按 `AGENTS.md` 第 2 节路由与档位（S/M/L）自动执行。
- **只读子代理**：
  - `explorer`：大范围定位代码与参照实现时派发，只返回 `file:line` 地图，保护主上下文干净。
  - `reviewer`：Tier M（>5 文件）或 Tier L 改动收尾前派发，按铁律、pitfalls 和 patterns 独立审查 diff。
- **代码图谱**：优先用 `python3 .harness/scripts/codegraph.py explore|query|node|callers|callees|impact|affected` 查询结构关系，再用 Grep 查文本配置。
- **生命周期 Hooks（见 `.claude/settings.json`）**：
  - `SessionStart`：增量同步代码图谱，注入当前任务、Git 与高频坑简报。
  - `PreToolUse`：保护 `.harness/scripts`、`.harness/.state` 与 `.env*`/密钥不被误改。
  - `Stop`：有代码改动但尚未完成 `task.py finish` 时自动提醒继续闭环；若需等待用户输入或确认，先运行 `python3 .harness/scripts/task.py pause "<等待原因>"` 再结束本轮。仅在用户明确要求时才可调用 `gate.py skip|off`。
