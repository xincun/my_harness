# 项目代码样板与可执行规约

> 只记录**本项目特有**的标准做法（Do / Don't）与黄金参照代码。通用工程分层、DI 与生命周期准则见 [architecture_principles.md](../guidelines/architecture_principles.md)。

## 1. 项目原语规约（onboard 与 learn 按需填写）

| 场景 | ✅ 本项目标准写法（Do） | ❌ 禁止写法（Don't） | 黄金参照锚点 |
|---|---|---|---|
| （例：金额计算） | 统一使用整数“分”（`int`） | 禁止用 `float` 存金额 | `shop/pricing.py#apply_discount` |

<!-- 写成下面格式（或在 .harness/project.json 配置 banned_patterns）时，kb_check.py 会自动扫描源码阻断违规（单行确需豁免可注 harness:ignore）：
- 禁用正则: `\bprint\s*\(` | 范围: `shop/*.py` | 原因: 业务代码禁止裸写 print，请用统一日志组件
-->

## 2. 标准实现样板（Canonical Patterns）

<!--
### PT001 · 如何新增一个 <接口 / 领域服务 / 任务处理器>
- **黄金参照实现**：`path/to/example.ext#Symbol`
- **联动修改清单**：1… 2… 3…
- **核心不变量与负向边界**：…
- **测试参照写法**：`path/to/example_test.ext#Symbol`
- **最后验证**：YYYY-MM-DD
-->
