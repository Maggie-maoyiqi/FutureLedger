# FutureLedger C4 Final Acceptance

## 1. Executive Summary

**最终状态：PARTIAL。** 已实现范围的 C-owned 离线检查全部通过；不是全系统验收通过。C1–C3 基线真实运行 145/145；C4 新增 44 项方法级测试，总计 189/189，无失败、无跳过。以下正式功能门槛尚有缺口：income/商户排行/月度趋势未进入正式 C/D 静态契约，公共 mandate 的 B scope 确认链路未完整接通；A/B/D 真服务、持久化恢复及生产 SSE 尚未就绪。

前置检查：从当前 Cloud 工作区已有 C3 `137e66f` 开发，C2 `6c61493`、C1 `7b3ef53` 均存在。起始工作区干净，未从旧 main 重建。独立分支 `codex/agent-final-acceptance`。仓库仍为 A–F 六人文档，没有新版五人分工。A/B/D 只有 Protocol、Unavailable 与测试假件，没有可执行真实服务或配置地址。基线保留 Demo/参考引擎结果与正式模块的区别。

最终测试 SHA、UTC 时间、命令/退出码/耗时、覆盖率、源文件 SHA256 与逐测试 ID 见 `c4-results.json`。之后的报告提交只修改文档，运行源文件与测试 SHA 对应内容一致。

## 2. Changes

下方为本轮完整文件清单（相对 C3）；不改动 A/B/D 算法、银行账本、Demo 或发布 gold 数据。

- `.github/workflows/c-module.yml`：新增：无密钥/无支付的本地质量 CI；保存可复现 artifacts。
- `contracts/application-api.c4.openapi.json`：新增：C4 OpenAPI，新增可选 chat 重放键，保留旧版快照。
- `docs/c4-integration.md`：新增：架构、工具/API/状态机、跨模块边界与本地接入。
- `docs/superpowers/plans/2026-10-29-agent-c4.md`：新增：基线、缺口、验收与审查计划。
- `requirements-c4.txt`：新增：沿用 runtime 依赖，加 Schema 校验/真实覆盖率/最小静态检查工具。
- `scripts/c4_unittest.py`：新增：逐方法记录结果、互斥分组及耗时。
- `scripts/check_c4_restart.py`：新增：双进程状态丢失验证，不冒称恢复。
- `scripts/prepare_c4_fixture.py`：新增：临时重建并核对源/gold，只复制忽略的 SQLite。
- `scripts/run_c4_benchmark.py`：新增：NL Agent/Mock cashflow 独立成绩 CLI。
- `scripts/run_c4_quality.py`：新增：质量门、SHA/时间/退出码/日志摘要/覆盖率汇总。
- `services/api/agent/adapters.py`：修改：Shanghai 日期归一化，固定只读分析口径不变。
- `services/api/agent/legacy.py`：新增：C1 B 操作的主体/版本/状态/并发/不重复提交边界。
- `services/api/agent/orchestration/api.py`：修改：可选 chat Idempotency-Key 接入，不影响冻结 C3 schema。
- `services/api/agent/orchestration/registry.py`：修改：typed 对象重验、诊断脱敏、交易范围与敏感结果校验。
- `services/api/agent/orchestration/replay.py`：新增：单实例有界请求合并、冲突检查与授权重检。
- `services/api/agent/orchestration/schemas.py`：修改：拒绝与硬约束矛盾的 feasible 声明。
- `services/api/agent/orchestration/service.py`：修改：成功业务节点与失败共存时返回 PARTIAL。
- `services/api/agent/sensitive.py`：新增：识别凭证模式并拒绝，明确非通用 DLP。
- `services/api/agent/service.py`：修改：模型前敏感输入/上下文拒绝、typed 意图重验。
- `services/api/app.py`：修改：C4 版本、legacy/replay 接入、无法恢复的 SSE 游标错误。
- `services/api/export_contracts.py`：修改：新增 C4 导出与漂移检查，旧快照保持原样。
- `tests/c4_benchmark.py`：新增：盲输入规划器、真实 Local Agent 路径和标注 oracle-backed 的 Mock D。
- `tests/c4_support.py`：新增：独立测试 Harness 与无账本 ContractGate。
- `tests/test_c4_contracts.py`：新增：OpenAPI 响应/JSON Schema/四意图补参越权与未支持能力测试。
- `tests/test_c4_faults.py`：新增：16 类故障注入与状态保留测试。
- `tests/test_c4_integration.py`：新增：业务链、legacy B 及丢失/回退状态测试。
- `tests/test_c4_security.py`：新增：认证/主体/注入/敏感数据/SSE/并发重放测试。
- `tests/test_c4_unit.py`：新增：UTC 边界、typed 对象/诊断、A 七能力、交易范围和 D 矛盾测试。
- `docs/c4-final-report.md`：新增：八部分最终验收与缺陷/兼容性结论。
- `docs/c4-results.json`：新增：提交、代码哈希、全部测试/基准/阻塞的真实机器证据。
- `docs/c4-pr.md`：新增：可直接用于 PR 的说明，未推送/未合并。

主要修复：上海业务日期过滤；工具结果日期/limit 和 D 可行性一致性检查；部分成功状态聚合；旧操作接口主体/版本/绑定验证；并发提交串行化；已成功及已提交操作的防重复转发；未知结果仅查询；结构化 SDK 对象重新校验；已识别凭证拒绝与序列化诊断脱敏；当前实例请求重放和 SSE 无效游标拒绝。

## 3. Test Matrix

计数按 unittest 方法分组，互不重叠；一个方法中的多组 subTest 不额外增加通过数。

|分类|PASS|FAIL|BLOCKED|NOT_RUN|SKIP|
|---|---:|---:|---:|---:|---:|
|C Unit Tests|6|0|0|0|0|
|C Integration Tests|12|0|0|0|0|
|Security Tests|10|0|0|0|0|
|Fault Injection Tests|16|0|0|0|0|
|Regression Tests|145|0|0|0|0|
|Agent Benchmark（Local 分析）|180|0|0|0|0|
|Cashflow Benchmark（Mock wire-only）|30|0|0|0|0|
|Real Service Integration（按 A/B/D 三个依赖计）|0|0|3|0|0|

- Reference Baseline：180 分析 + 30 现金流 = **210 PASS，0 FAIL**，仅参考引擎对齐。
- Reference Self-checks：**12 PASS，0 FAIL**；这 12 项也已在 145 项原有回归内，不能重复相加。
- Agent Analysis：**180 PASS，0 FAIL**；LocalPlanner → C1 → DAG → C3 registry → C1_fixture。只把自然语言问题、认证/授权语义上下文交给规划器；gold plan/expected/evidence_ids 均未进入规划器输入。输出后再与 gold 比对。不是 LLM 准确率，也不是正式 D 集成。
- Cashflow Integration：**30 PASS，0 FAIL，MOCK wire-only**；测试 D 直接返回发布 oracle 数组，验证实际 C→D 请求、工资变更版本、约束、假设、30 日响应传递。不计入 D 生产数值验证。
- Acceptance Specs：原 `benchmark/acceptance_cases.json` 的 **60 项仍为 NOT_RUN**，未改文件或真值。独立 overlay：**50 BLOCKED + 10 NOT_RUN（E sandbox）**。局部 C 测试覆盖不替代整项银行/审计/沙箱验收。
- API Schema：冻结 C1/C2/C3 快照检查、C4 OpenAPI 导出、JSON Schema 元校验、实际成功/错误响应校验通过。
- 故障 01–16 全部执行：LLM 超时/非法 JSON/非法字段/5xx，D 不可用、A 超时、B 创建丢失、未知状态、节点失败、独立节点部分成功、取消、工资重规划、成功资金节点保留、提交响应丢失、客户端重发、实例重置状态丢失。
- 另用两个独立 OS 进程验证旧 session/plan 为 403、不可用事件游标为 409。证明的是 state-loss fail closed，**不是跨进程恢复**。
- Ruff 仅 F821/F822/F823、pip check、原型检查、导出漂移与 diff 检查的实际退出码均记录在机器证据。

最终测试代码：`726d3e65adc050342319074484e98ad613f0c23d`；开始时工作区干净。UTC 2026-10-29T14:33:57.405108+00:00 至 2026-10-29T14:35:36.789367+00:00。测试 runner 耗时 82.491 秒，全部质量门的实际起止与各命令耗时见下表/机器证据。`services/api` 语句覆盖率 **91.29%**，分支覆盖率 **77.22%**，coverage 综合指标 **87.93%**；不是全仓覆盖率。远程 GitHub Actions 未运行，不能把本地 gate 结果当成远程 CI 状态。

|命令组|退出码|秒|
|---|---:|---:|
|fixture|0|0.079|
|dependencies|0|0.611|
|static|0|0.076|
|api_export|0|2.654|
|unittest|0|83.567|
|coverage|0|1.326|
|agent_benchmark|0|8.185|
|reference_oracle|0|0.317|
|reference_self_checks|0|0.313|
|prototype|0|0.088|
|process_restart|0|2.121|
|diff|0|0.005|

四类业务验收范围：

|业务|成功/正常链路|失败/缺参/越权|仍未满足的门槛|
|---|---|---|---|
|Analysis|Local fixture 180题及 Mock D evidence 流程|模型非法 JSON/Schema、D 失败、补参、主体/账户/dataset 拒绝|正式 D 缺失；收入/排行/趋势契约未扩展|
|Transfer|Mock B 提议，独立 C1 Mock B 确认/验证/执行/查询协议|缺额/收款人/日期、同名、保护只读、未确认、版本、丢失回包、并发重放|真实 B/A 与 C3 的公开授权接入缺失；无真实资金成功|
|Mandate|结构化路由、保护约束；registry 经 Mock B scope 后只读查询|缺参/越权/保护冲突、无 scope 拒绝、UNSUPPORTED|公共 API scope 后续查询/取消端到端尚未完整接通，不能声称成功取消|
|Goal|Mock D 参数/逐日结果、不可行原因、工资变更重规划|缺参/越权/D 不可用、可行性矛盾拒绝|正式 D 计算路径缺失；不虚构可行方案|

## 4. Security Findings

|级别|复现与影响|修复/回归|残余风险|
|---|---|---|---|
|HIGH，C 边界|并发 legacy execute 两次进入 B；回退 READY 可再次提交|并发回归已复现 RED（2 != 1）；每操作锁、执行尝试闩、成功保留；丢失回包后仅查询|B 仍须实现持久化幂等/授权，C 内存锁不能跨进程|
|HIGH，C 边界|旧公开接口直接转发任意操作/旧版本，缺少 C 自身绑定检查|本地绑定主体+计划+版本、会话授权重检、READY 前置；禁止 DAG 通过 C1 捷径确认|B 必须独立检查参数 hash/验证凭证/额度/银行状态；C 不替代 B|
|MEDIUM|已构造 Pydantic 上游对象可被修改而绕过默认验证|重新验证 dumped 字典；类型失配 RED/GREEN；关闭不可信序列化警告防止字段值出现在诊断中|服务适配器实现仍为可信部署代码；不是任意 Python 代码隔离器|
|MEDIUM|重复客户端 chat 创建多个计划/提议|可选键同主体/同 body 合并、冲突 409、命中重检授权，并发测试通过|无键为新请求；缓存最多256项且仅本实例；持久化幂等属 B|
|MEDIUM|交易返回跨日期/超 limit 仍被接受|范围/账户/limit 后置校验，拒绝上游扩大结果|D evidence 的真实所属/数值需正式 D 与银行证据验证|
|MEDIUM|LLM/工具回包、提示注入或已标记凭证污染上下文|封闭 Schema、未知工具拒绝、授权后端判定、凭证模式输入拒绝、工具/异常/事件测试|模式检测不是通用 DLP，未标记自由文本仍需产品级数据最小化|
|MEDIUM|D 声称 feasible 但最低余额低于硬约束|矛盾结果拒绝，不改 D 算法|无法凭 Schema 证明 D 算术正确，需正式 D 集成|
|MEDIUM，结果语义|已成功资金节点与独立失败同时存在却整体 FAILED|改为 PARTIAL，保存成功事实与节点结果|前端必须按节点及 B 权威状态展示|
|LOW，准确性|UTC 流水按字符串日期截断，上海月边界错误|UTC 边界最小复现原结果999、期望123；按上海业务日期过滤|本地 SQLite 仅合成分析 fixture，不能当 A 账本|
|LIMITATION|重启后内存 session/plan/events 消失|状态丢失测试明确拒绝旧状态，不冒称恢复；未来 SSE 游标409|无持久化 epoch，数值游标碰撞仍可能；生产恢复 BLOCKED|

未使用真实密码/OTP/银行信息；测试凭证均为显式 synthetic marker。C 不自动确认/验证/执行资金请求，强验证字段只转发 B，不进入模型或历史。没有发现可由不可信文本改变后端权限的已测试路径；这不等于形式化安全证明或生产渗透测试结论。

独立只读审查发现并发/成功状态回退及 typed-object 验证问题，均已修复并补充回归。审查者未声称独立运行全套测试，最终通过数来自本次实际 runner。

## 5. Contract Compatibility

|模块|当前可测状态|兼容性/外部依赖|
|---|---|---|
|A|MOCK；REAL BLOCKED|七项银行只读能力均有严格输入/输出、主体范围和失败测试。没有正式网络 API/账本；金额整数分，Shanghai 日期；C 无银行写接口|
|B|MOCK；REAL BLOCKED|Mock 只验证提议、显式确认/验证/执行、查询/取消、旧版/重发/未知状态边界。正式 hash、风险、OTP、幂等、恢复与额度均由 B 提供|
|D|C1_FIXTURE + MOCK；REAL BLOCKED|原 AnalysisPlan 和已定义 Cashflow Protocol 内验证兼容；正式服务不存在，不能证明正式协议兼容。docs/03 定义 income，但正式 C 静态 schema 仅三种支出指标；需联合扩展，不能改财务口径|
|E|OpenAPI/示例/状态说明 READY；实际前端 NOT_RUN|C4 新快照、统一 Envelope、错误与 SSE 限制已交付；C1/C2/C3 快照保持原样。没有替 E 实现全沙箱或前端|

C4 的共享契约变更仅为新增版本 OpenAPI 和 `/api/agent/chat` 可选 Idempotency-Key；新增错误码用于拒绝不安全/不一致结果，无原字段删除或改名。所有请求仍由认证上下文判定主体，body 不能切换 user_id；dataset 不扩大账户权限。新错误分支可能改变此前不安全调用的返回码，E 应依 OpenAPI/code 处理。

## 6. Known Limitations

1. PARTIAL：尚未满足完整四业务功能门槛；正式分析扩展、公共 mandate scope/取消集成需要 C/B/D 后续协作。
2. A/B/D 真服务 BLOCKED；真实 LLM、真实支付、远程 CI、完整 E 沙箱 NOT_RUN。
3. 无任务/会话/事件/重放的持久存储、生产 session TTL、分布式锁、跨进程恢复；开发认证不是生产认证。
4. 本地读调用可有限重试；资金有副作用调用不自动重试，UNKNOWN 交 B 查证。不能因内存状态丢失判断银行失败。
5. SSE 为有限当前实例回放，无持久化 Last-Event-ID/epoch；无完整生产断线续接。
6. 180 Local benchmark 是规则路径成绩；30 Mock cashflow 是 oracle-backed wiring 成绩，均不可包装成正式 D 或真实模型准确率。
7. 12 项自检与 210 oracle 成绩属于参考基线；60 全系统规格没有被宣称通过。
8. 凭证模式拒绝不能识别任意隐写/未标记敏感文本；日志/遥测生产部署仍需独立审查。依赖版本为上/下界范围，远程 CI 的实际安装与运行结果应重新留证。

## 7. Reproduction

从包含 C4 代码的干净分支（不是旧 main）执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-c4.txt
FUTURELEDGER_PROVIDER=local .venv/bin/python scripts/run_c4_quality.py --output /tmp/futureledger-c4-evidence
```

需要 Python3.12、Node24；无需真实 LLM Key 或银行服务。runner 会在缺少 ignored SQLite 时安全准备合成 fixture。测试不会提交真实支付。逐测试日志与 summary、coverage、agent-benchmark、unittest JSON 位于输出目录；CI 同样上传这些 artifact。测试环境若限制 TestClient 本地通信，需要环境允许本地测试，不应改测试断言绕过。

独立命令：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m services.api.export_contracts --check
.venv/bin/python scripts/run_c4_benchmark.py --output /tmp/c4-agent-benchmark.json
.venv/bin/python scripts/check_c4_restart.py
.venv/bin/python scripts/run_benchmark.py
.venv/bin/python -m unittest discover -s tests -p test_reference.py -v
```

本地启动、认证/聊天/重规划请求、工具表、状态机与接口边界见 [集成指南](c4-integration.md)。完整 OpenAPI 位于 `contracts/application-api.c4.openapi.json`。

## 8. PR Delivery

标题：**C4: harden Agent integration boundaries and add reproducible offline acceptance**。

分支保留为 `codex/agent-final-acceptance`，工作区代码已提交。建议 stacked base 为 C3 `codex/agent-orchestration`（`137e66f`）；如果 C1–C3 尚未合并，不应把本轮当作仅基于旧 main 的独立实现。完整 PR 文本见 [c4-pr.md](c4-pr.md)。没有创建远程 PR、推送或合并 main。
