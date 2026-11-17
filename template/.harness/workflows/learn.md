# 工作流：沉淀知识与单步收尾（在 `task.py finish` 前执行）

原则：**只记录下一个 Agent 光读代码和 `git log` 仍然极易做错的项目隐性事实**。宁缺毋滥，噪音比空白更有害。

## 第 1 步 · 准入四问（绝大多数任务直接答“否”跳到第 3 步）
一条经验要写进 `pitfalls.md`，以下四问必须**全部为“是”**：
1. **读代码也会错**：有经验的工程师只看当前源码和 `git log`，仍然很容易踩坑？
2. **项目特有**：来自本项目的架构边界、历史约束或外部耦合，而非语言/框架常识？
3. **会再遇到**：后续改同一模块或同类功能时还会碰到？
4. **结构性可复用**：能提炼为清晰的触发条件与正确做法，而非单次报错流水账？

不满足四问时的去处：
- 仅第 2、3 问为“是”（分散的模块内部约定）→ 补充一句话到 `docs/knowledge/modules/<模块>.md`；
- 重大架构权衡或机制替换 → 用 `.harness/templates/adr.md` 写入 `docs/knowledge/decisions/`；
- 能被机器直接拦截的分层禁令或禁用写法 → 直接登记为 `docs/ARCHITECTURE.md` 的 `禁止依赖: A -> B` 或 `patterns.md` 的 `禁用正则:`（`kb_check.py` 会自动将其变为硬门禁！）；
- 只与本次改动相关 → 写进 Git Commit Message，知识库不写（`--learned none`）。

## 第 2 步 · 归类写入（优先更新已有条目而非新建）
- 必须附**符号锚点** `` `path/to/file.ext#Symbol` ``，**严禁写易漂移的行号**（遗留行号可用 `python3 .harness/scripts/kb_anchors.py --fix` 自动转换）。
- 再次印证已有暗坑时，将其 `命中` 计数 `+1`（`≥ 2` 时 `/evolve` 会提示升级为铁律或自动门禁）。
- 新建 `docs/knowledge/` 文件后，同步登记到 `docs/knowledge/INDEX.md`。

## 第 3 步 · 唯一单步闭环（`task.py finish`）
全流程只在最后执行这一次收尾命令（内部自动串行完成：全量 `verify.py` → `kb_check.py` 断链/符号/图谱分层检查 → 验收项核销 → 采集会话步数与拦截指标写入 `tasks.jsonl`）：
```bash
python3 .harness/scripts/task.py finish \
  --type feature|bugfix|refactor|other \
  --outcome success|partial|failed \
  --covers "C1=<证据>" \
  --friction "最大阻碍（无则写 none；partial/failed 必填原因）" \
  --learned "更新的知识条目如 pitfalls#P002,modules/order（无则写 none）"
```
