<!-- harness:template — onboard 工作流完成后删除本行 -->
# 架构总览

> 读者：第一次接触本项目的工程师或 AI Agent。目标：5 分钟内看清“东西在哪、数据怎么流、谁绝不能依赖谁、核心不变量是什么”。

## 系统做什么
（2–3 句）

## 核心数据流
```
（外部入口）→（适配/接口层）→（核心领域层）→（存储 / 外部服务）
```

## 模块表
| 模块 | 职责 | 路径 | 允许依赖 | 模块卡片 |
|---|---|---|---|---|
| | | | | `docs/knowledge/modules/<模块>.md` |

## 分层与依赖规则（可执行 DEPS）
- 依赖方向：（例：`api/` → `service/` → `core/`，单向向下依赖；`kb_check.py` 会利用 CodeGraph 图谱自动拦截反向依赖并预警模块级环形依赖）
<!-- 写成下面格式（或在 .harness/project.json 配置 forbid_deps）时，kb_check.py 会自动扫描图谱与 AST 阻断违规：
- 禁止依赖: `shop/pricing.py` -> `shop/orders.py`
-->
- 通用组件切分、依赖注入与生命周期准则见 [docs/guidelines/architecture_principles.md](guidelines/architecture_principles.md)。

## 信任边界、并发与状态所有权
- **信任边界与幂等**：（哪些入口接收外部不可信输入？在何处做校验、鉴权、默认拒绝 Fail-Closed 与防重？）
- **状态所有权与生命周期**：（核心可变状态由哪个实体唯一持有？跨线程/跨进程如何同步与平滑排空回收？）

## 外部依赖
| 依赖 | 用途 | 配置位置 |
|---|---|---|
