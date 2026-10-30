# PR title

feat(agent): add controlled tool orchestration and evidence-based reports

# PR description

依赖 C2 分支 codex/agent-dag-replanning（6c61493），C3 审阅范围为 6c61493..HEAD。C1/C2 尚未合并时使用堆叠 PR 或先集成依赖，不能将 C3 提交视为可独立移植到旧 main。

C1/C2 已有严格意图和版本化 DAG，但缺少统一的工具策略、跨模块适配、完整调用链与状态聚合。本 PR 新增 13 项受控工具注册、严格输入/输出校验、有限只读重试、超时/节点/深度/总预算，复用现有 ChatProvider 和 C2 调度器。

新增 A/B/D Protocol 与显式注入边界；默认接口未就绪返回 UNSUPPORTED。明确转账意图只向 B 创建待确认提议并查状态，不自动确认/验证/执行；代扣读取保留 B 的黄色范围确认。新增聚合报告、证据/假设与脱敏关联事件，区分 READY_FOR_EXECUTION、等待确认、强验证、未知、真实/模拟完成。成功和未知资金事实不因取消或版本变化被改写，取消需 B 绑定回执。

新增 7 类 /api/agent 路径和独立 C3 Schema/OpenAPI；C1/C2 契约快照、原 Demo 与业务真值不变。跨模块服务尚未存在，Mock 仅位于测试；生产认证、B 执行门/Worker、D 数值算法、持久存储与事件恢复不在此 PR 中。

验证：145/145 全量 unittest（新增 Registry/Provider14、Mock 场景/API23、原有108）；10 个要求场景全部实际运行。离线 oracle210/210、原型及依赖/契约检查、默认开发工厂真实 HTTP 冒烟通过。真实 Provider 与 A/B/D 真服务未运行，60 条银行/恢复/沙箱验收仍 NOT_RUN，Mock 成功不计作真实银行成功。

独立审查的两项 Important（状态引用绑定、明确取消后的未知状态）均有失败复现及修复回归。使用示例、模块依赖、完整文件清单和 C4 集成任务见 `docs/c3-orchestration.md`。
