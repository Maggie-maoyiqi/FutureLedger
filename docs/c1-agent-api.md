# C1 Agent 与应用 API

## 现状、范围与决策

本轮依据用户给出的 C1 任务直接实现；用户已授权合理假设与连续实施。2026-10-29 检查未发现新版五人分工，`05-六人分工与里程碑.md` 仍为 A–F 六人制。本交付沿用 C/A/B/D/E 的接口所有权，不重新分配 F 的职责。基线为 `8c525ecb51c63eeb2e2cb69f995fd5b4621f37a5`，已有 26 个 unittest 通过。

现有 `demo/` 是演示应用，SQLite 是合成分析夹具，`db/schema.sql` 是未经生产验证的 PostgreSQL 设计，60 条 acceptance 仍是 NOT_RUN。现有代码不是正式银行服务。本轮创建 `services/api/agent/` 和独立 FastAPI app，不改变 Demo。

选择独立应用、依赖注入和保守 C1 子集。直接扩展 Demo 会混入演示支付/口径；整体迁移 Demo 会影响其他成员，均不采用。复用 Demo 的 Provider HTTP 适配器、有限离线分析解析、只读 SQLite 连接和 SQL 审核；夹具分析使用符合旧契约的固定表达式，禁止调用演示写方法。默认服务拒绝认证；本地开发显式注入令牌与合成主体，不提供生产登录实现。使用环境中已有的 Undertaker2077/FutureLedger checkout，不重写为 Maggie-maoyiqi 的 origin，也不覆盖其他成员远程工作。

## 契约差异（新增版本，非破坏性替换）

- `docs/03`、静态语义契约将 transfer 排除在消费外，Demo 将它计入。C1 的分析严格限定现有静态 analysis-plan v1 的三种指标、单类别、单值；夹具适配器以固定表达式排除 transfer，复用 Demo 的只读连接和 SQL 审核，拒绝 Demo runtime 数据。D 扩展前不得静默采用 Demo 新口径。
- 新增 `agent-intent.c1.schema.json` 与 `application-api.c1.openapi.json`，不修改已有共享契约。C1 的 plan 是提议，不是 B 的 operation，也不是 D 已完成的现金流方案。
- `/api/sessions` 为会话创建的必要新增接口；`/api/chat` 新增显式 `context_refs`，允许用户选择可见历史，模型不得扩大集合。历史引用限同主体、同会话，不携带银行授权。
- `/api/analysis` 使用 `{session_id, plan: AnalysisPlan}`；`/api/operations.selected_action` 是固定动作枚举，实际参数从服务端计划快照读取，不接受模型/浏览器参数覆盖。确认、验证、执行协议保留给 B；本轮返回 UNSUPPORTED，不生成授权或银行结果。
- `/api/events` 提供内存事件的有限 SSE 回放；每帧 data 采用统一 envelope。断线可用 Last-Event-ID 重连；非持久、非完整实时订阅，不宣称 worker 恢复。JSON 错误在打开流前返回。

## 组件与安全边界

严格 Pydantic 模型检查六个必需键 intent/entities/constraints/missing_fields/context_refs/proposed_steps 及嵌套字段，禁止额外字段、布尔金额、浮点/字符串金额和非法 ISO 日期。金额整数分。模型建议不生成权限、认证、风险或成功状态。

认证适配器返回服务端 Principal（subject、账户授权、数据集授权）；会话绑定创建时的授权快照，每次访问重检当前授权。会话、计划、上下文、任务、事件均限制主体；请求不接受 user_id。账户还须匹配目录所有权。夹具目录的收款人只表示合成目录中的 verified，银行实际验证/报价仍是 A/B 的职责。

后端重新计算缺参、同名消歧、来源账户和日期合法性。上下文只复用已解析无歧义的字段，需显式引用或“还是上次那个”等明确指代；重新校验当前权限、日期和收款人。不能复用模型原始文本、失效/取消计划或授权。不同意图不互相继承实体。

只读约束和保护代扣约束在会话内单调累积，模型不能撤销；“别动云盘”由后端记录保护偏好，不是取消授权。C1 没有解除保护/只读的接口，需要新会话或后续专用偏好接口。代扣读取仍需 B 的范围确认，C 不直接返回授权列表。模型的 proposed_steps 被后端重建为提议步骤；缺参、只读或受保护的操作不能提交到执行接口。

默认 ExecutionGateway/CashflowGateway 明确不可用。`ports.py` 定义 B 的 propose/confirm/verify/execute/get 和 D 的 simulate，均接收服务端认证主体与类型化参数。B 每次检查所有权、版本、摘要、认证、幂等和额度；D 只做计算，参数不得带写权限。C1 没有实际执行网关；接入前还须设计持久化 operation 关联及提交/取消竞态。C1 取消仅针对自己持有且未提交的规划任务，不声称取消银行操作。

## C2–C4

- C2：有限 DAG 的节点/边 Schema、无环验证、工具能力白名单、预算和超时；按依赖执行只读节点，写节点只提议给 B；测试环、非法依赖、部分失败、取消及工具注入。
- C3：持久化会话/计划版本、事件与恢复；重规划绑定数据版本、假设和约束；修改参数使 B 旧确认失效；工资延期、旧版本竞态、跨主体和进程重启测试。
- C4：接 A/B/D 真实协议、Vue 联调、持久 SSE 与游标过期恢复、限流与可观测性；独立统计真实模型准确率、延迟、工具失败和端到端银行验收，不混用 fake/oracle 分数。

## 已知限制

内存状态为单进程、重启丢失，不能用于多 worker/生产；缺少生产认证、持久数据库、配额、DAG、重规划和正式支付。离线解析规则有限，未知含义应追问，不等同模型理解准确率。当前依赖沿用 requirements-demo 的范围版本，未新增锁文件；正式发布前由团队锁定版本与许可证。外部 LLM 需要另行配置密钥、模型和网络。

## 交付文件

全部为新增文件；原有应用、测试、依赖、数据库设计、共享静态契约及真值文件不变。

|文件|用途|
|---|---|
|`services/__init__.py`, `services/api/__init__.py`, `services/api/agent/__init__.py`|正式 Python 包边界|
|`services/api/app.py`|FastAPI 组合根、统一响应/错误、认证和 11 类业务路径|
|`services/api/dev.py`|显式开启的合成主体开发工厂|
|`services/api/export_contracts.py`|C1 契约导出与漂移检查|
|`services/api/README.md`|启动、调用示例、API 行为和验证命令|
|`services/api/agent/schemas.py`|严格六字段意图、嵌套实体、请求/响应 Schema|
|`services/api/agent/auth.py`|认证协议、不可变 Principal、默认拒绝/显式开发认证|
|`services/api/agent/context.py`|会话、上下文、提议、取消和事件内存存储|
|`services/api/agent/planner.py`|Planner 协议、本地规则、ModelPlanner 适配器|
|`services/api/agent/service.py`|业务校验、追问、消歧、引用合并和提议门禁|
|`services/api/agent/money.py`|不受 Decimal 上下文舍入影响的精确元转分|
|`services/api/agent/adapters.py`|A 合成目录与 D 只读分析夹具边界|
|`services/api/agent/ports.py`|B/D 类型化交接协议和 UNSUPPORTED mock|
|`services/api/agent/errors.py`|统一脱敏业务错误|
|`tests/test_agent_c1.py`|46 个 C1 schema/流程/权限与边界测试|
|`contracts/agent-intent.c1.schema.json`|新增版本意图 JSON Schema|
|`contracts/application-api.c1.openapi.json`|新增版本完整 OpenAPI 3.1|
|`docs/c1-agent-api.md`|设计、契约差异、交付与后续任务|
|`docs/c1-pr.md`|可直接用于 PR 的标题与描述|
|`docs/superpowers/plans/2026-10-29-agent-c1.md`|实施计划与验证记录|

## 验证证据（2026-10-29）

- 全量 `unittest discover -s tests -v`：72/72，通过，其中 C1 46 个、原有 26 个。包括四意图、缺参、同名消歧、未知字段/类型/金额/日期、主体/账户/数据集/上下文隔离、注入、无效模型 JSON、超时/错误、只读门禁、保护、旧约束失效和 Demo 回归。
- `scripts/run_benchmark.py`：210/210 离线 oracle 对齐；原型：7 路由 + 30 现金流情景 + 工资延期交互通过。均不表示 Agent/银行验收成绩。
- `pip check` 无依赖冲突；C1 Schema/OpenAPI `--check` 无漂移；`git diff --check` 通过。
- 真 HTTP 启动开发工厂，验证认证会话、四意图、同名追问、餐饮净支出 221842 分、D 返回 UNSUPPORTED、SSE 回放和服务 OpenAPI。烟测使用临时随机令牌，结束后关闭 C1 进程；原 Demo 健康检查仍 ready。
- 独立代码审查发现两项 Important：代扣名称替换时残留旧 ID、最低留存与操作金额混淆。已先写失败回归，再修复；额外验证长小数不能被 Decimal 默认精度静默舍入为整数分。
- 测试有一条继承自现有依赖的 Starlette/httpx 弃用警告；未降低断言、屏蔽验证或改动业务真值。未测试真实 LLM、生产认证、真实银行、完整 60 条银行/恢复/沙箱验收、浏览器与 Vue；60 条规格仍 NOT_RUN。

完整启动与验证命令见 `services/api/README.md`。该模块可独立审阅合并，但必须经 B/D/E 对交接契约复核后再接真实模块。
