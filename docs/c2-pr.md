# PR title

feat(agent): add typed DAG orchestration and safe incremental replanning

# PR description

C1 只保存单轮意图提议，尚不能表达多节点依赖、按变更局部失效或阻止旧版本继续编排。本 PR 新增有限、类型化 DAG：拓扑排序、循环/缺失依赖拒绝、工具白名单和参数验证、前置条件、独立节点状态及失败下游阻断。

新增动态重规划，金额/收款人/付款日/工资日变化及合法上游事实不符时递增 plan_version，记录字段/结果差异和原因，只失效受影响读节点及下游。成功资金节点保留原参数、原始版本和回执；冲突记录为 COMPLETED_ACTION_IMMUTABLE，不自动重付或补偿。所有待完成 B 提议须经 B 失效确认才能切换版本，未知提交只查证。

C 通过 A/D 只读协议与 B prepare/observe/lookup/invalidate 协议编排，无银行提交或执行方法。默认适配器返回 UNSUPPORTED；Mock 结果标识 mock_A/mock_B/mock_D。新增 8 类 API 路径及 C2 Schema/OpenAPI，C1 契约快照不变；C1 提议提升后禁止旧入口旁路，已有/在途 C1 操作因缺少失效协议而拒绝提升。

验证：全量 unittest 108/108（新增 C2 36 + 原有 72），离线 oracle210/210，原型检查、pip check、契约漂移检查和默认开发工厂真实 HTTP 冒烟通过。独立代码审查的四项 Important 均有失败复现及修复回归。真实模型准确率、真实银行、生产恢复与 60 条银行/恢复/沙箱验收没有宣称通过。

已知边界：单进程内存状态、无持久审计/跨进程锁；实际 B 必须实现原子凭证失效、版本检查、幂等和恢复。代扣范围确认及现金流数值仍待 B/D 正式接入。运行、API 示例、结果来源与依赖说明见 `docs/c2-dag-replanning.md`。
