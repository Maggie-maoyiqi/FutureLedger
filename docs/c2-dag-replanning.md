# C2：显式 DAG 与动态重规划

## 范围与决策

基于 C1 `7b3ef53`，在独立分支实现；基线 72 项 unittest 通过。仓库没有更新的五人分工，继续沿用 docs/05 的 C 职责及 A/B/D 所有权。不改银行、Worker、数值真值或原 Demo。

选择显式有限图、类型化工具目录和增量版本引擎。不采用自由代码 Agent（无法约束工具）或一次性重跑全图（可能重复资金动作）。延续用户已授权的连续实现方式，不引入额外的设计审批暂停。使用 brainstorming/writing-plans 整理边界，test-driven-development/executing-plans 实施，最终独立审查。

模型/请求只提交 NodeDraft：node_id、tool_name、inputs、depends_on、preconditions、expected_result。risk_level、status、result_ref、compensation 均由服务端生成；风险标记仅供显示，不能替代 B 的权限判断。图最多 64 节点，拒绝重复 ID、缺失依赖、环、未知工具及非法输入/条件。

工具目录只包含 A 的余额、收款人、报价；D 的分析、现金流；B 的转账/取消代扣提议及范围确认边界。C 不调用银行写接口、B.execute 或补偿转账。资金节点需要显式向 B 准备提议，运行时只查询 B 已绑定的操作。默认 A/B/D 适配器不可用；开发环境仅复用明确标识的 C1 分析夹具。测试 Mock 的来源始终为 mock_A/mock_B/mock_D。

## 版本与安全

每次重规划使用乐观 plan_version 加一，保留旧版本快照、字段差异、原因、受影响节点和冲突。只重置输入变化节点及其下游；独立已完成读节点复用 result_ref。已成功的资金节点及其原参数/结果不可修改、不得重提；请求修改该动作时记录 COMPLETED_ACTION_IMMUTABLE 冲突，不能把已发生事实改成新金额。

所有旧版本停止调度。所有尚未完成的 B 提议须获得 B 的失效回执后才能切换版本；新版本必须重新准备提议，绝不迁移确认或验证凭证。SUBMITTING/PENDING_UNKNOWN 不允许直接重规划：先由 B 查询与恢复，C 不盲目重提。成功动作保留原始版本来源，不宣称新版本再次执行成功。

工具结果按白名单响应 Schema 校验；不能添加权限、工具、节点或修改图结构。合法响应不符合显式 expected_result 时产生 upstream_changed 新版本，保存实际结果并使下游失效；非法响应使节点失败，只阻断下游，独立节点可继续。条件只能读取有依赖关系的节点的类型化结果。

每个计划通过锁串行化运行/重规划/提议，版本在锁内检查。保留 C1 主体/会话/账户快照校验，执行前重新检查目录和上海业务日。只读模板移除资金节点，手写只读图中的资金节点被拒绝；保护代扣仍生效。C1 提议提升为 DAG 后，旧 C1 写/模拟入口被阻止，避免旁路版本门禁。

## API 与兼容

新增 `/api/dag/plans`、`/api/plans/{id}/dag`、`/api/dag/plans/{id}`、`.../run`、`.../replan`、`.../reconcile`、`.../operations`、`.../results/{result_ref}`。保留统一 envelope、主体认证和 C1 API。C1 契约快照保持不变，C2 单独导出 DAG Schema 与完整 OpenAPI。C1 的自然语言解析不承担图权限；先得到校验后的 C1 意图，再通过确定性 Planner 建图。

`reconcile` 只查询 B。即使上游变化已阻止普通调度，仍可查明已绑定操作；准备提议回包丢失时，只能由 B 按稳定的 plan_id/plan_version/node_id 查找已有操作，不会再次 prepare。B 没有 lookup 能力就保持未知、交由 B 恢复。旧 C1 操作缺少失效协议：如果提升之前已有成功创建、在途或结果未明的 C1 操作，直接返回 LEGACY_OPERATIONS_EXIST，不能声称迁移已撤销旧凭证。

## 仍未提供

进程内计划和结果存储不等于持久审计或 Worker 恢复；跨进程锁、崩溃恢复、生产认证/限流、真实 A/B/D API 集成留待后续。B 适配器必须实现原子失效与版本检查，C2 的 Mock 测试不是银行幂等或授权验收。取消代扣/范围确认没有正式 B API 时保持不可用。compensation 是人工复核元数据，绝不自动撤销成功支付。

读结果缓存没有生产 TTL；B 必须在执行时重新检查余额、报价有效期、收款人与策略。C2 的初步金额/最低余额检查不是银行授权。来自不同账户范围的缓存不会扩大权限，但不能据此保证未来支付可行。当前 B prepare/observe/lookup/invalidate 是协议，未接入真实服务。

## 运行与 API 示例

依赖及安全生成本地开发令牌步骤沿用 `services/api/README.md`，从仓库根目录启动：

```bash
export FUTURELEDGER_PROVIDER=local
export FUTURELEDGER_DEV_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m uvicorn services.api.dev:create_dev_app --factory --host 127.0.0.1 --port 8001
```

所有业务请求携带 Bearer header，不将令牌写入 URL。以下请求体中的 session/plan ID 都来自服务器响应：

1. `POST /api/sessions`：`{"dataset_id":"personal_ledger"}`。
2. `POST /api/chat`：`{"session_id":"<session>","text":"从acc_daily给妈妈转25元，明天"}`。
3. `POST /api/plans/<C1 plan_id>/dag`：`{"plan_version":1}`。返回独立 DAG plan_id，模板为 balance + recipient → quote → payment。只读请求模板没有 payment。
4. `POST /api/dag/plans/<dag plan_id>/run`：`{"plan_version":1}`。默认 A 未接入，所以对应节点明确 FAILED/UNSUPPORTED，下游 BLOCKED，不返回模拟支付成功。分析意图可用开发工厂已有的只读夹具，结果标记 C1_fixture。
5. `POST /api/dag/plans/<dag plan_id>/replan`：`{"plan_version":1,"reason":"user_changed","updates":{"amount_cents":3000}}`。返回 v2、字段差异、受影响节点和保留结果。收款人、付款日、工资日分别使用 recipient_id、execution_date、salary_date；不是任意输入字典。
6. 对旧版本调用 run/replan/operations 返回 PLAN_STALE。`GET /api/dag/plans/<id>?version=1` 可查看 SUPERSEDED 历史快照；`GET .../results/<result_ref>` 只允许查看本人此计划的结果。
7. A 读取节点成功且业务约束满足后，`POST .../operations`：`{"plan_version":2,"node_id":"payment"}`，只向 B 准备版本绑定的提议。默认 B 不可用返回 UNSUPPORTED；不生成确认/验证凭证，不提交付款。
8. B 在其独立流程处理确认/执行后，再调用 `POST .../run` 或 `POST .../reconcile`：`{"plan_version":2}`，C 只读取 B 的权威状态。未知结果必须先 reconcile；新版本需要重新准备提议。DAG 操作不能使用 C1 confirm/verify/execute 绕过此边界。

工资日重规划示例（仅适用于包含 cashflow.simulate 节点的图）：

```json
{"plan_version":1,"reason":"user_changed","updates":{"salary_date":"2026-11-17"}}
```

`POST /api/dag/plans` 也接受严格手写/模型输出草稿，例：

```json
{"session_id":"<session>","nodes":[{"node_id":"balance","tool_name":"bank.balance","inputs":{"source_account_id":"acc_daily"},"depends_on":[],"preconditions":[],"expected_result":[{"field":"available_cents","value":900000}]}]}
```

如果 A 合法返回 800000 而预期为 900000，返回新版本，`diff.result_changes` 记录 expected=900000、actual=800000 和结果引用。模型不得提交 risk_level/status/result_ref/compensation；这些字段只出现在服务端 DagPlan 中。Schema 的拓扑、工具输入配对和跨节点条件关联仍以运行时验证器为准。

## A/B/D 依赖

|模块|C2 协议|默认行为及边界|
|---|---|---|
|A|ReadTools：余额、收款人、报价，始终传 Principal 与类型化输入|默认 UNSUPPORTED；不实现银行账本，不直接提交转账|
|B|BankExecution.prepare/observe/lookup/invalidate|按稳定计划/版本/节点绑定操作；B 独立决定权限、额度、幂等及恢复；失效回执须明确匹配全部旧提议|
|D|ReadTools：analytics.analysis、cashflow.simulate|分析开发路径复用只读 C1_fixture；默认现金流不可用，不计算或伪造未来余额|
|C1|已验证 ChatResult + Principal/ContextStore|确定性模板、共享主体/约束检查、统一 SSE envelope；提升后禁止旧写/模拟旁路|

## 文件与验证

- 新增 `services/api/agent/dag/`：schemas.py（草稿/状态/回执/差异）、graph.py（拓扑/祖先/下游）、planner.py（C1 模板）、ports.py（A/B/D 协议）、engine.py（调度/增量版本/查证）、api.py（8 类新增路径）及 __init__.py。
- 修改 `services/api/app.py`：注入 C2，接入路由和 C1 迁移门禁；`export_contracts.py` 分别导出原 C1 与新增 C2。
- 新增 `contracts/dag-plan.c2.schema.json`、`contracts/application-api.c2.openapi.json`。原 C1 契约和原静态分析契约不变。
- 新增 `tests/test_dag_c2.py`（24 项）、`tests/test_dag_api_c2.py`（12 项）；设计、实施记录和 PR 描述位于 docs。

验证命令：

```bash
FUTURELEDGER_PROVIDER=local .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_benchmark.py
node scripts/check_prototype.cjs
.venv/bin/python -m pip check
.venv/bin/python -m services.api.export_contracts --check
git diff --check
```

2026-10-29：全量 108/108（C2 36、原有 72）通过；离线 oracle 210/210、原型 7 路由/30 情景/延期交互、依赖和契约检查通过。独立审查的 4 个 Important 问题已先复现为失败测试，再修正并全量回归：未知 prepare 重试、会话锁后约束复核、重规划中 B 查证、旧 C1 提议及在途迁移。测试不计作真实模型准确率、银行执行或生产恢复验收。原依赖有 Starlette/httpx 弃用警告，不影响本轮结果；60 条银行/恢复/沙箱验收仍 NOT_RUN。

真实 HTTP 冒烟：启动默认开发工厂，验证 C1 分析提升为 DAG、运行并读取 C1_fixture 结果 221842 分；A 未接入明确报告 UNSUPPORTED；金额重规划生成 v2，旧版本与旧 C1 入口拒绝、reconcile 和 SSE 可用、服务 OpenAPI 一致。使用临时随机本地令牌，无真实 LLM/银行调用，结束后关闭测试进程。
