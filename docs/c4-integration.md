# C4 集成与安全交付指南

本轮基于当前 Cloud 工作区 C1 `7b3ef53`、C2 `6c61493`、C3 `137e66f`，独立分支 `codex/agent-final-acceptance`。不回退旧 main，不实现 A 的账本、B 的授权执行门、D 的财务计算、E 的前端。仓库仍只有六人分工文档；没有发现新版五人文档，沿用现有接口所有权。

最终验收结论与逐项证据见 [C4 报告](c4-final-report.md) 和 `c4-results.json`。正式服务的集成状态为 BLOCKED，离线质量门通过不等于全系统验收通过。

## 架构与边界

认证适配器生成 Principal → Session/ContextStore 校验主体与授权范围 → C1 LocalPlanner/ModelPlanner 生成封闭意图 → 服务端校验/补参/约束合并 → C2 DAG 编译、依赖排序与版本校验 → C3 固定 ToolRegistry → 有界 Orchestrator → A/B/D Adapter → 确定性聚合器 → Envelope。

已有细节分别见 [C1](c1-agent-api.md)、[C2](c2-dag-replanning.md)、[C3](c3-orchestration.md)。C4 增加的是边界修复和实际验证，不增加银行或数值引擎。模型与不可信工具文本均没有权限授予通道；工具名、输入/输出 Schema、重试和可用性来自后端注册。封闭 Schema 拒绝 SQL、authorized、risk_level 等越界字段。SDK 返回的 Pydantic 对象也必须重新验证，不能因已构造为对象而绕过类型检查。

13 个注册能力：`get_balance`、`list_transactions`、`resolve_recipient`、`quote_transfer`、`get_transfer_status`、`list_mandates`、`get_mandate_status`、`analyze_transactions`、`simulate_cashflow`、`compare_scenarios`、`propose_transfer`、`propose_mandate_cancellation`、`get_operation_status`。`GET /api/agent/tools` 返回各自的 Schema、available、provenance、是否有副作用及超时/重试策略。没有银行写工具或任意 SQL 工具。代扣读取必须取得 B 的 scope，不能用用户偏好替代授权。

C1 FixtureAnalysis 仅对合成 SQLite 运行固定参数化只读 SQL；C4 将日期过滤统一到 Asia/Shanghai。D 计算结果必须与请求/约束一致；C 只检查可行性声明矛盾，不改变 D 的计算公式。交易返回不得超出来源账户、日期范围或 limit。

## 状态和重规划

节点独立记录结果及引用，失败阻断依赖，独立成功节点保留。成功的资金节点与独立失败并存时整体为 PARTIAL，不能将已成功资金操作误报为整体 FAILED。

修改金额/收款人/执行日/工资日会产生新版本；只重算受影响节点和下游，保留成功事实。旧版本确认失效，成功资金节点不能重复提议。未知提交结果只允许查询 B，由 B 负责幂等、恢复和最终授权。

C3 主状态：NEEDS_INFO、BLOCKED、AWAITING_CONFIRMATION、NEEDS_AUTH、READY_FOR_EXECUTION、SUBMITTED、PENDING_UNKNOWN、COMPLETED、MOCK_COMPLETED、PARTIAL、FAILED、CANCELLED、CANCEL_REQUESTED、INTERRUPTED。READY 表示授权准备状态，不表示提交成功；MOCK_COMPLETED 必须在 UI 明确展示模拟来源。取消本地任务不代表撤销银行事实。

原 C1 `/api/operations` 系列与 C3 DAG 的 B 操作是两条明确边界：DAG 绑定的操作不能通过旧 C1 路径绕开新校验。正式 B 需要提供其确认/验证/执行前端流程；目前不能把 C3 提议变成一个已端到端接通的生产支付流程。

旧 C1 操作经 LegacyBoundary 绑定主体/计划/版本；调用前重新检查会话授权。每操作串行处理，成功状态保留。一旦执行请求已到达 B，本实例不再次提交同一操作；即使丢失回包后的查询返回过期 READY，也不得重新付款。只有明确查询/恢复流程处理未知状态。C 不校验 OTP 真伪，不决定最终风险；测试中的 ContractGate 是无账本的协议假件。

## API 与 E 接入

完整 OpenAPI：[application-api.c4.openapi.json](../contracts/application-api.c4.openapi.json)。C1/C2/C3 的已有快照保持原样。所有 JSON 响应为 `{data,error,request_id}`；错误包含 code/recoverable/next_action，HTTP 错误不能仅凭 200 响应体推断。

|接口|用途/边界|
|---|---|
|POST /api/sessions|认证主体创建会话，dataset 不能扩大授权|
|POST /api/chat|C1 结构化理解、补参、操作提议|
|POST /api/analysis|C1 严格 AnalysisPlan；默认 D 未接入返回 UNSUPPORTED|
|POST /api/plans/{id}/simulate|C1 CashflowGateway，默认 UNSUPPORTED|
|POST /api/operations|C1 向 B 提议，不执行银行写入|
|POST /api/operations/{id}/confirm、verify、execute|显式转发独立 B 流程；execute 必须 Idempotency-Key|
|GET /api/operations/{id}|读取本实例绑定的 B 操作；不支持凭任意 ID 发现操作|
|POST /api/tasks/{id}/cancel|C1 本地取消|
|GET /api/events|当前实例有限 SSE 回放，主体隔离|
|POST /api/agent/chat|C1→C2→C3 受控流程，新增可选 Idempotency-Key|
|GET /api/agent/tools|能力声明与 Schema|
|POST /api/agent/plans/{id}/run、replan、reconcile、cancel|版本校验、重规划、仅查询未知状态、取消请求|
|GET /api/agent/plans/{id}/report|当前任务状态和来源证据|
|/api/dag/*|C2 显式 DAG 创建/查询/运行/操作提议；详见 OpenAPI|

错误示例：401 AUTH_REQUIRED；403 FORBIDDEN；409 PLAN_STALE/PENDING_UNKNOWN/IDEMPOTENCY_CONFLICT/EVENT_CURSOR_UNAVAILABLE；422 INVALID_REQUEST/SENSITIVE_INPUT/UNKNOWN_TOOL；501 UNSUPPORTED；502 INVALID_TOOL_RESULT/INVALID_UPSTREAM_RESULT/INVALID_INTENT/SENSITIVE_CONTEXT；503 UPSTREAM_UNAVAILABLE/CAPACITY_EXCEEDED。执行可能已被 B 接收时返回 PENDING_UNKNOWN，不当作明确失败或自动重试付款。

C4 新增的 chat Idempotency-Key 为 ASCII 1–128 字符。相同主体+键+请求体共享一次有界流程；换请求体返回 409。缓存命中仍校验当前会话与授权，更新 request_id。客户端重发须使用相同键；无键调用保持旧行为，是新的独立请求。最多保存 256 项，不逐出金融提议证据；满时 fail closed。该机制是单实例保护，不能替代 B 的持久化幂等。多进程/重启场景仍需 B 与持久存储。

SSE 支持当前实例内排序回放及 Last-Event-ID；不存在的未来游标返回 409。游标没有持久化 epoch，重启后相同数值可能碰撞，不能承诺完整断线续接。前端重启后必须重新认证、查权威任务，不能把“没有事件”解释为没有付款。

## 最小本地运行与请求样例

仅使用合成数据、LocalPlanner 和离线分析；不需 LLM Key。默认 `services.api.app:app` 拒绝所有认证，开发工厂必须显式启用。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-c4.txt
.venv/bin/python scripts/prepare_c4_fixture.py
export FUTURELEDGER_PROVIDER=local
export FUTURELEDGER_DEV_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/uvicorn services.api.dev:create_dev_app --factory --host 127.0.0.1 --port 8765
```

另一个本地终端配置同一私有 token（不要把它提交或粘贴到报告），调用：

```http
POST /api/sessions
Authorization: Bearer <private-dev-token>
Content-Type: application/json

{}
```

```http
POST /api/agent/chat
Authorization: Bearer <private-dev-token>
Idempotency-Key: analysis-request-001
Content-Type: application/json

{"session_id":"<returned-session>","text":"2026-09的餐饮支出是多少？"}
```

离线样例返回 COMPLETED、221842 分及证据，source 为 C1_fixture/offline_fixture；不能展示为 D 真服务结果。转账文本例 `给妈妈转25元，2026-11-02，来源acc_daily` 在未配置 A/B 时只能产生阻塞/不支持结果。目标例 `买电脑，目标存6000元，2026-11-19，来源acc_daily，至少保留3000元` 需要 D 正式实现或测试 Mock。

```http
POST /api/agent/plans/<plan>/replan
Authorization: Bearer <private-dev-token>
Content-Type: application/json

{"plan_version":1,"reason":"user_changed","updates":{"salary_date":"2026-11-17"}}
```

## 可复现质量门

```bash
FUTURELEDGER_PROVIDER=local .venv/bin/python scripts/run_c4_quality.py --output /tmp/futureledger-c4-evidence
# 单独复现原有及新增测试
FUTURELEDGER_PROVIDER=local .venv/bin/python -m unittest discover -s tests -v
# 只检查导出漂移
.venv/bin/python -m services.api.export_contracts --check
```

runner 保存分支、SHA、工作区是否有修改、代码 SHA256、UTC 起止、每命令耗时/退出码/日志摘要、逐测试结果和真实覆盖率。覆盖率仅对 services/api；ruff 仅检查未定义名称 F821/F822/F823，不声称完整风格检查或安全扫描。CI 使用 Python3.12、Node24、无真实凭证、local provider、只读内容权限；CI 配置在本地验证，远程 GitHub Actions 执行另行确认。

`prepare_c4_fixture.py` 在临时目录重建，验证生成文件与发布数据逐字节一致后只复制 Git 忽略的 SQLite。不会通过重写 gold、改动 D 口径或删除测试掩盖错误。

## 测试解释与已知限制

- 180 分析题：真实 C1/C2/C3 路径、LocalPlanner、C1_fixture，gold 只在输出后比对，不能称 LLM 准确率/正式 D 集成。
- 30 现金流题：OracleWireD 直接返回发布真值，仅验证请求、重规划版本、约束/假设和逐日响应传递。不能作为 D 算法正确率或生产路径成绩。
- 210 参考题与 12 参考自检独立报告；12 自检同时包含在全仓回归中，不能重复加到总测试数。
- 60 验收规格原文件保持 NOT_RUN；overlay 将全链外部依赖列为 BLOCKED、E 沙箱列为 NOT_RUN。局部 C 用例通过不代表完整规格通过。
- 正式分析契约没有 income/ranking/trend。Demo 支持更多能力，但不能当作正式 C/D 已实现；需 C/D 联合扩展严格契约及定义，禁止单方改变 docs/03 真值。
- 公共 mandate scope 授权后续流程没有完整接通；只验证 registry 的 B scope 校验、保护约束和不支持拒绝。C4 不虚构成功取消。
- 任务/会话/事件/重放缓存全在内存。双进程测试只证明重启后旧状态不可用且拒绝访问，不证明恢复。没有生产会话 TTL、持久化审计、分布式锁或跨进程幂等。
- 已识别的凭证模式在进入模型/工具响应前拒绝；独立 verify 字段为 SecretStr，不进入上下文/事件。不保证识别任意未标记敏感自然语言，不宣称完整 DLP。
- 真实 LLM、A/B/D、支付、沙箱和远程 CI 未运行。真实金融部署前必须补齐认证、持久存储、B 的幂等/权限/恢复和真实集成验收。
