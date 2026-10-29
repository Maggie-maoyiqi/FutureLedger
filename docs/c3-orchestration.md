# C3 LLM、工具注册与执行编排

基于 C1 7b3ef53、C2 6c61493；基线 108 项测试通过。使用现有隔离 checkout 的独立分支，不重建旧 main，不覆盖 A/B/D/E。仓库仍为六人 A–F 分工，未发现新版五人文档；A/B/D 正式 API 尚不存在。

## 设计与边界

复用 C1 AgentService/ModelPlanner、demo.provider.ChatProvider、C2 DagEngine/版本/权威结果；C3 增加固定工具目录、超时/重试策略、A/B/D 适配器、预算、聚合与事件。选择包装和少量可选事件/状态钩子，不复制模型客户端或 DAG 调度器。用户已明确要求连续实现并运行测试，沿用既有合理假设执行方式。

工具注册只接受固定白名单，不注册 submit_transfer、cancel_mandate 直连工具，也不将确认/OTP/执行接口暴露给模型。输入/输出均按严格 Schema 校验，未知字段拒绝。只读瞬态故障可有限重试；B 的提议/取消绝不自动重试。所有处理重新检查主体、账户范围、版本与保护约束；代扣读取需要由 B 验证的明确范围，不能靠模型说“已授权”。

自动调用链：C1 校验意图 → C2 模板/图校验 → C3 Registry 包装的 A/D → C2 状态和结果引用 → 明确转账意图才串行准备 B 提议 → 查询 B 状态 → 确定性结构化汇总。无自动 execute/confirm/verify。已有独立验证 API 的凭证只走 B，不进入模型、工具日志或事件。

默认限制为 32 节点、12 层、并发 1、30 秒总预算；可由服务器以有界配置调整。串行只读调用是有限并发的一种，不并发提交提议。保留 C2 低层 API；C3 预算施加在 C3 编排入口，低层 C2 本身仍受 64 节点和逐调用超时约束。

模型请求只复用已有 ChatProvider；增加有界瞬态重试包装，非法 JSON/Schema 不重试为“成功”。Local 继续使用原本的有限规则；Fake 只存在于测试。工具载荷不再送回模型，摘要由已验证的结构化结果生成，金融数字来自 D/B，不由 LLM 计算。

C2 的旧版本门禁、凭证失效回执、成功资金节点不可重复执行、未知状态只查证保持不变。取消只停止本地尚未派发的后续工作；已有 B 操作必须通过 B 的 cancel_unsubmitted_task 回执确认。已提交/未知事实保留，不伪装为失败、撤销或已退款。

B 的 READY 显示 READY_FOR_EXECUTION，不能当作 SUBMITTED；NEEDS_CONFIRM、NEEDS_AUTH、SUBMITTING/PENDING_UNKNOWN 分别保留等待确认、强验证、查证语义。Mock B 的成功只显示 MOCK_COMPLETED。取消回执附带来源和绑定信息；B 明确确认 CANCELLED 时解除对应未知状态，但不会解除其他操作的未知状态。取消后 B 查证为成功，仍保留成功事实，不能改写为“已撤销”。

事件使用已有内存事件通道，新增受控事件类型及 request_id/task_id/plan_version/node_id 关联；不保存密码、OTP、密钥、隐藏思维链、用户问题或完整工具载荷。仅支持当前进程内回放，不承诺持久 SSE、跨进程 Last-Event-ID 或生产恢复。

## 工具与模块契约

|模块|逻辑工具/接口|边界|
|---|---|---|
|A| get_balance、list_transactions、resolve_recipient、quote_transfer、get_transfer_status、list_mandates、get_mandate_status |仅事实读取，Principal 和来源账户后端限定；状态响应必须匹配请求引用；代扣读取先向 B 检查范围|
|B|propose_transfer、propose_mandate_cancellation、get_operation_status|只向 B 创建提议或查状态；不注册银行 submit_transfer/cancel_mandate 直连，也不注册自主 execute/confirm/verify|
|B Adapter Protocol|create_operation、confirm_operation、request_verification、execute_operation、get_operation_status、cancel_unsubmitted_task、lookup_operation、invalidate_operations、check_mandate_scope|确认/验证/执行供独立 B 人工交互流程集成；本轮 C3 自动流程不调用这三种方法。旧 C1 独立接口及 C2 门禁保持不变|
|D|analyze_transactions → execute_analysis_plan；simulate_cashflow；compare_scenarios → compare_cashflow_scenarios|所有数值来自 D；C3 不写 SQL 或实现现金流算法；分析返回 evidence_ids，现金流保留假设、约束、逐日余额与冲突原因|
|E|消费 ExecutionReport 和元数据 SSE|按机器状态展示；不能根据 summary 或模型文本授予权限；独立验证表单不进入聊天|

Registry 内共 13 个逻辑工具。保留 C2 的 bank.balance/bank.recipient/bank.quote/analytics.analysis/cashflow.simulate 别名和有限 NodeDraft，不扩展旧 C2 枚举。事务列表、状态查询、场景比较作为受控内部能力存在，不开放任意 invoke HTTP 接口；不是每个目录条目都对应当前 C1 的自由文本意图。

ToolSpec 包含唯一名称、输入/输出模型、模块/版本、能力、适配器、是否副作用、超时、有限尝试和确认要求。GET 目录的 available 表示已配置实现声明，不是权限或健康证明；provenance 明确标识 A/B/D、mock_A/mock_B/mock_D 或 C1_fixture。模拟适配器全部位于 tests，运行模块没有默认模拟银行。

本地开发工厂继续可显式使用既有 C1 只读分析夹具，此桥接标记 C1_fixture，不是 D 的正式服务；默认 API 未配置分析时仍返回 UNSUPPORTED。A/B 与现金流没有正式服务时保持不可用，不用模拟成功消除阻塞。

## C1 → C2 → C3 调用链

1. 认证适配器生成 Principal，C1 会话绑定主体、数据集与账户快照。
2. C1 的 Local 或 ModelPlanner（复用 ChatProvider）输出严格 AgentIntent；服务端再做缺参、同名、日期、账户与约束校验。未满足时 C3 返回 NEEDS_INFO/BLOCKED，不运行工具。
3. C2 DagPlanner 从有效 C1 提议建图，继承主体与只读/保护约束；C2 校验依赖、工具参数和版本。
4. C3 校验节点数/深度/总预算；C2 逐节点调用 RegistryReads。Registry 在调用前/输出后严格校验，使用 A/D Protocol；只读瞬态故障最多 2 次，Schema/权限错误不重试。
5. C2 保存通过校验的结果引用和来源；失败阻断下游。C3 保存 D 的完整报告供聚合，C2 仍使用原兼容的数值/状态模型。工具载荷不回传模型。
6. 明确转账意图且依赖事实满足时，C3 串行调用 C2.prepare → RegistryGate → B.create_operation，一次提议，无自动执行。查询 B 的状态并返回等待确认/强验证/查证等真实机器状态。
7. 重规划使用原 C2 递增版本/结果保留/凭证失效回执；C3 只重跑受影响节点。旧 C1 提议提升限制和旧版本门禁不变。
8. 确定性聚合 task_id、plan_id、plan_version、node_statuses、execution_status、summary、evidence_refs、assumptions、next_action、outputs、cancellations 及 request_id；继续采用 `{data,error,request_id}`。

## 启动和调用示例

仓库根目录，依赖沿用 requirements-demo.txt，不需要新增包。详细安全令牌配置见 `services/api/README.md`：

```bash
export FUTURELEDGER_PROVIDER=local
export FUTURELEDGER_DEV_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m uvicorn services.api.dev:create_dev_app --factory --host 127.0.0.1 --port 8001
```

令牌仅用于本地合成主体，默认 `services.api.app:app` 没有可用认证。启动外部模型仍使用 README 的环境变量；不在文件或聊天中提交真实密钥。当前 OpenAPI 为 0.3.0。

|方法/路径|请求|行为|
|---|---|---|
|POST /api/agent/chat|C1 ChatRequest：session_id/text/context_refs|完整意图→图→工具→聚合；缺参先追问|
|POST /api/agent/plans/{id}/run|plan_version|有界运行已有 DAG，不自动创建新写提议|
|POST /api/agent/plans/{id}/replan|C2 ReplanRequest|更新版本、使旧提议失效并重算受影响节点|
|POST /api/agent/plans/{id}/reconcile|plan_version|只读 B 查证/lookup；不重付|
|POST /api/agent/plans/{id}/cancel|plan_version|停止后续编排；已创建的操作由 B 确认能否取消|
|GET /api/agent/plans/{id}/report|认证主体|当前结构化聚合，不重跑工具或模型|
|GET /api/agent/tools|认证主体|工具 Schema、策略、模块版本、声明可用性与来源|

先 `POST /api/sessions`，请求 `{}`，取得 session_id。其余请求均携带 Bearer header，不将凭证放进 URL。以下 text 使用服务器返回的会话：

```json
{"session_id":"<session>","text":"2026年9月餐饮净支出是多少？"}
```

本地分析夹具可返回 221842 整数分和证据，明确来源 C1_fixture；D 正式接口需另行接入。

```json
{"session_id":"<session>","text":"从acc_daily给妈妈转25元，明天"}
```

在 Mock 集成测试中得到 A 核验/报价与 B 的待确认操作，不自动付款；默认开发环境 A/B 未接入时节点报告 UNSUPPORTED/阻断。`给张伟转25元` 应追问候选，不能自行选择。

```json
{"session_id":"<session>","text":"取消云盘代扣"}
```

受保护云盘返回 BLOCKED，不能创建取消提议。一般代扣读取也不能只凭模型声称“已授权”运行。

```json
{"session_id":"<session>","text":"从acc_daily存6000元买电脑，2026-12-01，至少留3000元"}
```

购机目标传递 600000 分与最低 300000 分给 D；默认现金流不可用。初始工资日明确为 2026-11-12 的计划可用以下请求将工资延迟五天（POST /api/agent/plans/{id}/replan）：

```json
{"plan_version":1,"reason":"user_changed","updates":{"salary_date":"2026-11-17"}}
```

Mock 测试在同一个显式目标/付款复合图验证工资日修改只重算现金流，并使该图已有 B 确认失效。C 不猜测未提供的工资日，不把“延期五天”的自然语言当作已授权的新支付。

## 测试与真实结果

执行：

```bash
FUTURELEDGER_PROVIDER=local .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_benchmark.py
node scripts/check_prototype.cjs
.venv/bin/python -m pip check
.venv/bin/python -m services.api.export_contracts --check
git diff --check
```

测试分类（2026-10-29）：

|类别|结果及范围|
|---|---|
|新增边界/单元测试|14：Registry 11 + Provider 3；含白名单、严格 I/O、范围、黄色授权、重试/超时、引用绑定及 HTTP MockTransport。不是供应商真实调用|
|新增 Mock 场景/API 集成|23：包含要求的全部 10 场景，以及预算、深度、取消、强状态分类、证据/事件、跨用户和敏感字段回归|
|原有回归|108：C1/C2、数据/参考计算及 Demo，保持通过|
|合计|145/145 实际运行通过|
|真实 Provider|未运行，没有真实模型准确率/延迟成绩|
|A/B/D 真服务|未运行，仓库尚无正式服务；本轮只验证适配器契约和 Mock 链路|
|原离线 oracle|210/210，仅参考对齐，不计作模型或银行验收|
|原型/其他|原型 7 路由 + 30 情景 + 延期交互通过；依赖检查和契约检查通过|
|本机真实 HTTP 冒烟|默认开发工厂启动成功；C1→C2→C3 分析 221842 分及证据、同名追问、云盘保护、A 未接入时阻断转账、工具目录与脱敏事件验证通过。来源为 C1_fixture，不计作 A/B/D 真服务集成|
|未运行验收|60 条银行/恢复/沙箱验收仍 NOT_RUN；生产并发/恢复、浏览器/Vue、真实银行和真实数值引擎集成未验证|

10 场景均有独立 test_scenario_01…10：指定月餐饮分析及证据；6000 购机/最低 3000；同图工资延期与旧确认失效；查验收款人及报价后待确认；同名追问；云盘保护；B 未知只查证；恶意备注实际进入工具边界且不改变权限；模型跨账户拒绝；模型超时/非法数据无副作用。

HTTP 冒烟使用临时随机开发令牌，未输出或保存真实凭证，结束后关闭本次启动的测试进程。真实 Provider 与正式模块缺失不会通过默认 Mock 静默补齐。

独立审查发现 2 项 Important：状态事实未匹配请求引用、明确取消回执后残留未知状态。均先写失败回归，再修复并完整回归。额外验证 READY 不等于已提交、查证完成事件关联、预算中断不将未知银行结果标记为失败。现有依赖的 Starlette/httpx 弃用警告保留，未降低断言或改动业务真值。

## 文件清单与安全检查

- 新增 `services/api/agent/orchestration/`：`schemas.py`（C3 模型/报告/预算）、`adapters.py`（A/B/D 协议与不可用默认）、`registry.py`（固定目录/调用校验）、`runtime.py`（C2 桥接）、`provider.py`（现有客户端包装）、`service.py`（编排/聚合/取消）、`events.py`（脱敏事件接口）、`api.py`（7 类认证接口）、`__init__.py`。
- 修改 `services/api/agent/dag/engine.py`：可选 Registry 节点桥接、访问/观察/事件钩子及中断状态保留，不替换 C2 的版本/资金门禁。
- 修改 `services/api/app.py`、`dev.py`：显式注入 Provider/A/B/D，默认保持拒绝认证或依赖不可用；`export_contracts.py` 独立导出 C1/C2/C3 快照。
- 新增 `contracts/agent-report.c3.schema.json`、`contracts/application-api.c3.openapi.json`；原 C1/C2/静态分析契约不变。
- 新增 `tests/c3_fakes.py`、`tests/test_tools_c3.py`、`tests/test_orchestration_c3.py`。Fake Provider、Mock A/B/D 和假密钥只用于测试，无真实银行数据或密钥。
- 新增本交付说明、实施记录及 `docs/c3-pr.md`；更新服务 README 的 C3 指引。

安全检查结果：无注册银行直写工具；无自动 confirm/verify/execute；只读请求不生成资金节点；主体/数据集/账户/上下文及版本门禁回归通过；代扣范围需 B 明确核验；凭证字段不被 chat 接受，错误不回显；事件不含原问题、完整载荷或授权信息；Mock 成功有独立状态/来源；PENDING_UNKNOWN 不被当作失败，不重复创建/付款。

## 已知限制与接口阻塞

状态、工具结果和事件仍在单进程内存，不能保证重启、多 worker、跨任务恢复或生产审计。C3 取消信息作为当前编排状态与 B 回执展示，原 C2 历史模型保持兼容；应通过 C3 report 获取包含取消证据的当前视图。C2 低层接口不是 C3 预算入口。适配器可信部署代码必须遵守 Protocol，不能用模块名称代替远端认证。

自然语言 Local 解析有限，复杂场景仍需明确参数/版本化 API。工资延期不是自动支付授权。D 数值只在 Mock/原参考数据中验证；正式 D 的假设、口径、证据、计划约束契约需搭档确认。B 必须提供原子旧版本失效、稳定操作查找、独立验证、幂等及查证；C3 不自行实现这些能力。13 工具的其他读取/比较能力需要对应正式 A/D 实现，不能由“已注册”推断服务可用。

## C4 待办

接入 A/B/D 的真实传输与身份配置、冻结跨模块版本及契约；验证 B 原子失效/幂等/未知恢复；持久化计划/结果/事件、跨进程协调、游标过期恢复；Vue 联调；独立真实 Provider 准确率与银行验收、限流/隐私与脱敏审计。当前不实现银行账本、额度锁、Worker/Outbox、现金流数值算法、前端或沙箱。

|阶段|依赖与任务|进入下一阶段的验收条件|
|---|---|---|
|C4.1 契约/传输|A/B/D 搭档冻结版本、状态枚举、来源与错误；实现真实 HTTP/SDK Adapter 和安全认证配置|同一套 Adapter 契约测试对真实服务通过；范围/引用/超时/不支持均有可验证响应|
|C4.2 执行联调|B 提供独立确认/验证/执行、旧版本失效和稳定查找；E 接确认与验证表单|旧 params_hash/授权不能跨版本；未知结果不重付；已提交或成功动作不能被取消回执伪装为未提交|
|C4.3 持久状态/事件|接团队应用存储与 B 恢复流程；持久化版本、结果、审计事件，验证跨进程互斥与游标过期|重启/双 worker/断线后状态可核验，无重复银行动作；SSE 重连重新查询权威状态|
|C4.4 独立验收|分别运行真实模型意图集、D 数值真值、A/B 银行故障/并发、安全及 Vue 联调|结果按真实模型、确定性计算、Mock、真实银行分开报告；未执行项不计入通过|
