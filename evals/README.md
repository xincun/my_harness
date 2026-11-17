# 固定评测基线

```bash
PYTHONDONTWRITEBYTECODE=1 python3 evals/run.py --output /tmp/harness-eval.json
```

`cases.json` 固定任务、干扰条件与预期完成状态。runner 只使用 demo 项目的临时副本，回放功能实现、遗漏验收、回归、删除、等待、零检查、写入冲突、并发交接与日志丢失。独立行为 oracle 在候选仓库外，不依赖 verify 返回的绿灯。

`baseline.json` 是当前版本的本地协议基线，包含源文件指纹、各场景耗时、误报完成和未分类错误。重新跑的耗时可能变化，比较时先看行为与覆盖范围。精细故障与真实进程竞争由 `tests/test_completion.py`、`tests/test_concurrency.py` 覆盖。

这组数据不经过大模型，不代表编码成功率、Superpowers 对比或 token 节省。未知 token、成本、人工介入保留 null。不要把“测试通过率”改称“agent 成功率”。

下一步真实 agent 对照沿用第一条 shipping_fee 任务作为起点，另加入实际项目里固定的 bug、跨模块功能和中断交接任务；锁定仓库起点、模型、宿主版本、预算，每组重复运行，留出未调优任务。行为判定来自独立测试 / 运行结果，完成声明来自 `stop_gate.py --check`。记录任务的 token、费用、人工介入和耗时后，再比较不同 harness。修改规则只先发布为候选，回归或独立验收下降就不晋升。
