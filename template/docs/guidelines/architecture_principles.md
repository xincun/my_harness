# 通用工程架构与对象交互设计准则

> **背景与渊源**：本文档汲取自 Chromium 团队在千万行超大规模工业级工程治理中沉淀的核心架构与交互原则（源自 Chromium `//chrome/browser design principles` 与 `Layered Components` 规范）。
> 我们剥离了其浏览器专有的概念（如 Browser/Tab/WebContents/GN），提炼为适用于**通用工业级软件工程**（无论是后端微服务、中间件、桌面端、移动端还是 CLI 工具）的对象交互、模块切分与代码组织铁律。

---

## 核心设计心法总览

1. **显式优于隐式，窄依赖优于宽依赖**：消灭上帝对象（God-Object），只注入当前组件最小所需的依赖。
2. **状态单向流动，杜绝反向回调与重入**：下层服务只管向下执行，绝不在执行期间反向回调上层调用方。
3. **交互模型纯粹化，消灭环形依赖**：环形依赖是混用 Pull（拉取）与 Push（推送）的典型征兆；一个交互链路要么纯拉，要么纯推。
4. **特性间平级解耦，杜绝网状感知（$O(N^2) \to O(N)$）**：两个平行业务特性严禁直接感知彼此的存在，通过统一仲裁者或互斥令牌解耦。
5. **回调时序确定性（Don't Release Zalgo）**：一个 API 的完成回调要么永远同步，要么永远异步，严禁受内部缓存影响产生双态。
6. **生产与测试同源，拒绝测试特判**：通过依赖注入（DI）在装配阶段注入替身，严禁在生产业务代码中插入 `if (is_test)` 或防御式空指针检查。

---

## 一、模块切分与依赖方向（Modularity & Boundaries）

### 1.1 严格分层：纯核心（Core）与驱动适配层（Driver/Adapter）物理隔离
- **核心逻辑（`core/`）**：只包含业务模型、规则算法与纯内存状态演进，**严禁依赖任何外部平台、框架或 I/O 驱动层**（如操作系统 API、Web/UI 框架、数据库驱动、特定进程模型），通过抽象接口与外部交互。
- **适配驱动（`driver/` / `adapter/` / 平台目录）**：负责环境感知、协议编解码、外部 I/O 与平台特定实现，单向向内依赖 `core/`。
- **目录与 `DEPS` 落地范式**：每个分层子目录通过 `ARCHITECTURE.md` 的 `禁止依赖: core -> driver`（由 `kb_check.py` 基于 CodeGraph 图谱强制执行）锁死单向边界，使 `core/` 可在零外部依赖、纳秒级纯内存单测中验证，彻底消除环境脆化（Flakiness）。

### 1.2 彻底消灭上帝对象（God-Object Anti-pattern）
- **现象与危害**：将承载整个应用上下文的大对象（如 `Context`、`App`、`Browser`、`GlobalState`）作为参数层层透传给子模块。这会导致：
  - 依赖关系完全黑盒化，无法看清模块真正用了什么；
  - 极其容易产生隐藏的跨层耦合和循环依赖；
  - 单元测试无法单独实例化被测组件，必须构造极其庞大且脆弱的 Mock 全局对象。
- **铁律**：**组件需要什么，构造函数就显式只注入什么。**

```python
# ❌ 反模式：注入上帝对象
class DiscountCalculator:
    def __init__(self, app_context: AppContext):
        self.context = app_context

    def calculate(self, order_id: str):
        # 内部从上帝对象身上掏出深层状态
        user_tier = self.context.user_service.get_tier(order_id)
        config = self.context.config_manager.get("discount_rules")
        return ...

# ✅ 遵循原则：精细构造依赖注入（Constructor DI）
class DiscountCalculator:
    def __init__(self, tier_provider: UserTierProvider, rules: DiscountRules):
        self._tier_provider = tier_provider
        self._rules = rules

    def calculate(self, user_id: str, amount_cents: int) -> int:
        tier = self._tier_provider.get_tier(user_id)
        return self._rules.apply(tier, amount_cents)
```

### 1.3 状态归宿与精准生命周期（Lifecycle Semantics）
- **禁止无生命周期的伪全局单例**：状态必须附着在明确的生命周期所有者上（如：请求级作用域 Request-scoped、会话级 Session-scoped、引擎级 Engine-scoped）。
- **生命周期对齐**：
  - **严禁长周期对象持有短周期对象的引用**（典型内存泄漏源头）；
  - **严禁短周期状态上浮存储在长周期对象中**（跨请求状态污染、多线程竞态与拖拽/迁移失效）；
  - 销毁时必须自顶向下级联释放，子组件在父组件生命周期结束前全部注销完毕。
- **跨进程与有状态实体的退场（Passive Cascade & Graceful Drain）**：
  - **被动级联回收**：跨进程、子代理或外部会话协作时，优先绑定底层句柄/管道/连接生命周期，借通信底座天然断开实现被动回收，严禁假设外部进程永远能主动发“优雅退出”通知；
  - **平滑排空态（Drain）**：有状态实体在关闭或切换瞬间严禁直接抹空内存状态导致在途异步回调/滞后消息抛硬异常，应设置短暂排空态容忍或安全丢弃滞后事件。

---

## 二、对象交互与控制流（Object Interaction & Control Flow）

### 2.1 彻底消灭重入与反向回调（Eliminate Re-entrancy）
- **反模式**：调用方调用下层服务时，下层服务在执行中途，又通过 Delegate/回调反向查询调用方，或者触发事件导致调用方在同一调用栈内再次进入当前方法。
- **后果**：破坏了调用方的状态一致性不变量（Invariant），造成不可预测的中间态、死锁或重复执行。
- **重构心法**：
  1. **参数前置计算**：调用方在调用下层方法前，准备好所有决策入参，一次性传入；
  2. **状态单向流动**：数据与控制流严格单向流转，下层只返回结果，绝不反向向调用方索要裁决。

```python
# ❌ 反模式：下层在执行中反向回调上层（重入风险）
class OrderService:
    def execute_order(self, order_id: str, delegate: OrderDelegate):
        order = self.repo.get(order_id)
        # 执行中途反向向上层要决策
        if self.is_high_load() and not delegate.should_proceed_on_high_load():
            return None
        return self._do_execute(order)

# ✅ 遵循原则 1：入参前置传入，控制流单向流转
class OrderService:
    def execute_order(self, order_id: str, bypass_high_load: bool = False):
        order = self.repo.get(order_id)
        if self.is_high_load() and not bypass_high_load:
            return None
        return self._do_execute(order)

# ✅ 遵循原则 2：下层暴露纯判断 API，上层统一调度，无重入
class OrderController:
    def handle_submit(self, order_id: str):
        if order_service.is_high_load() and not self.user_is_vip():
            return "Server busy"
        return order_service.execute_order(order_id)
```

### 2.2 纯粹的 Pull 或 Push，消灭环形依赖
- **根因洞察**：绝大多数对象间的环形依赖（A 依赖 B，B 依赖 A），本质是**在同一个交互流程中混用了 Pull（拉取状态）和 Push（推送通知）模型**。
- **重构铁律**：
  - **纯 Pull 模型（主调驱动）**：主控层单向依赖所有被调模块，主动读取状态并决定下一步操作。依赖图形成自上而下的树。
  - **纯 Push/Observer 模型（事件驱动）**：发布者对订阅者完全无感知（只依赖通用的 EventBus 或抽象 Listener 接口）；订阅者单向感知发布者的事件，严禁发布者在事件触发阶段再反查订阅者的内部状态。

```
❌ 混用模型（环形依赖）:
Showcase ──(调用/依赖)──> FeatureA ──(依赖)──> FeatureB
   ▲                                              │
   └───────────────(反向通知/依赖)─────────────────┘

✅ 方案一：纯 Pull 模型（上层单向依赖下层）
Showcase ──(调用/依赖)──> FeatureA
Showcase ──(调用/依赖)──> FeatureB
（FeatureA 与 FeatureB 不反向依赖 Showcase，由 Showcase 主动拉取决定展示）

✅ 方案二：纯 Push/观察者模型（单向广播解耦）
FeatureA ──(发射事件)──> EventHub / Listener
FeatureB ──(发射事件)──> EventHub / Listener
Showcase ──(订阅事件)──> 接收变更，自驱刷新
```

### 2.3 回调函数时序确定性（Deterministic Callback Timing）
- **Release Zalgo 反模式**：同一个 API 在不同分支下，一会儿同步执行回调，一会儿异步执行回调（例如：内存缓存命中时同步直接回调，未命中走网络 I/O 异步回调）。
- **危害**：调用方无法确定代码执行顺序，导致由于调用时机突变引发的竞态条件和死锁。
- **铁律**：**一个异步 API 的回调必须永远在未来的事件循环微任务/事件队列中异步执行，即便数据已经存在于缓存中，也必须通过 dispatch_async / microtask 延迟到本轮调用栈退出后触发。**

```python
# ❌ 反模式：时序二义性（一会儿同步，一会儿异步）
def get_user_data(user_id: str, callback):
    if user_id in cache:
        callback(cache[user_id])  # 同步回调！调用方调用此行下方的代码还没走完！
    else:
        network.fetch(user_id, callback=callback)  # 异步回调！

# ✅ 遵循原则：永远保证异步确定性
def get_user_data(user_id: str, callback):
    if user_id in cache:
        event_loop.post_task(lambda: callback(cache[user_id]))  # 确保脱离当前栈执行
    else:
        network.fetch(user_id, callback=callback)
```

---

## 三、特性解耦与复杂度控制（Orthogonal Features & Decoupling）

### 3.1 拒绝 $O(N^2)$ 网状感知，降维至 $O(N)$
- **反模式**：每当新增一个业务特性，该特性就要在内部硬编码检查所有现存其他特性的状态；而现存特性又被改动加上对新特性的检查。导致代码复杂度按 $O(N^2)$ 爆炸。
- **铁律**：**平级特性之间互相完全不可见。** 特性间的互斥或资源协调，必须通过**中介者（Mediator）**或**作用域令牌（Scoped Token）**进行正交仲裁。

```python
# ❌ 反模式：O(N^2) 网状互斥，新增特性时必须修改所有老特性
class SparkleBanner:
    def show(self):
        # 强耦合其他完全无关的特性！
        if print_preview.is_showing():
            return
        if dev_tools.is_showing():
            return
        if modal_dialog.is_showing():
            return
        self._render()

# ✅ 遵循原则：通过仲裁者获取独占令牌，复杂度降为 O(N)
class SparkleBanner:
    def __init__(self, modal_coordinator: ModalCoordinator):
        self._coordinator = modal_coordinator
        self._active_token = None

    def show(self):
        # 申请模态互斥锁，SparkleBanner 根本不知道 PrintPreview / DevTools 的存在
        self._active_token = self._coordinator.try_acquire_modal_scope("sparkles")
        if self._active_token:
            self._render()
```

---

## 四、全局状态与作用域纯净度（Global Hygiene）

### 4.1 全局函数仅允许纯函数（Pure Functions）
- **允许**：无副作用、不读取或修改外部可变状态的纯工具函数（如数据格式化、编解码、纯算法校验）。
- **严禁**：通过全局静态方法隐式读取或修改全局非托管状态（如 `GlobalRegistry.get_current_user()`、`DBManager.execute_global()`）。
- **要求**：有状态的操作必须作为实例成员由持有该状态的对象执行，或通过参数显式传入。

---

## 五、可测性与防劣化哲学（Testability & Invariants）

### 5.1 严禁在生产代码中为测试打补丁（No Test-only Conditionals）
- **反模式**：因为难以构造测试环境，而在生产逻辑中添加测试专用分支：
  ```python
  # ❌ 绝不能在生产代码中为测试开天窗
  if getattr(config, "IS_TEST_MODE", False):
      return mock_response
  if view is None:  # 单测里懒得 mock view，所以留个防御性判空
      return
  ```
- **正解**：通过显式接口与构造注入，在测试装配阶段（Test Setup）注入轻量级 Fake/Stub 实现，**生产代码的执行路径对测试环境完全透明无感知**。

### 5.2 拒绝变更检测测试（No Change Detector Tests）
- **反模式**：单测逻辑几乎是生产代码的逐行镜像重写（比如用一系列 mock 校验 `mock_a.call(); mock_b.call()`）。这种测试在业务重构时必然变红，但从未测出过任何真正的 bug。
- **铁律**：**单元测试只验证契约、输入输出映射、不变量与边界错误**，不测试实现内部调用招式。

### 5.3 消除无法到达的死分支（No Unreachable Branches）
- 业务代码中的每一个 `if/else` 分支，都必须有明确清晰、可被测试覆盖的真实业务场景或故障注入场景。
- 严禁为了“心理安全”编写永远不可能触发的幽灵防御分支；如果代码在逻辑上不可能到达，使用断言（Assert / Invariant check）直接防御，而不是默默吞掉或留下一段未受测代码。

---

## 落地指引：在项目中如何落地本准则

1. **新模块入库时**：
   - 依赖项只入构造函数，不传 Context；
   - 检查是否有下层向调用方反向查询的代码，若有立即改为参数前置；
2. **审查代码时**：
   - 是否存在循环依赖？若是，找出 Pull/Push 混用点，单向化重构；
   - 多个特性互斥时，是否形成了网状感知？若是，抽象为中介者/互斥令牌；
3. **架构体检工具**：
   - 使用 `.harness/scripts/kb_check.py` 扫描架构文档与 `DEPS` 依赖规则，在 CI 门禁中坚决阻断反向依赖。
