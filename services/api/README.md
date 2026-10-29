# C1 Agent & Application API

当前分支提供 C3 受控工具编排、A/B/D 协议、结构化聚合、取消和事件关联，详见 [C3 交付与 API 示例](../../docs/c3-orchestration.md)。默认应用 OpenAPI 版本为 0.3.0，C1/C2 契约快照分别保留；完整链路入口为 `/api/agent/chat`，原 `/api/chat` 仍为 C1 提议接口。

当前分支保留已实现的 C2 显式 DAG、增量重规划和 B 查证入口，详见 [C2 使用与交付说明](../../docs/c2-dag-replanning.md)。启动命令不变，C2 的 0.2.0 契约快照和原 C1 契约快照保持不变。

正式 C 模块的首个可合并版本；不是生产支付系统。原 `demo.api:app` 保持独立可运行。完整设计、差异及 C2–C4 任务见 [交付说明](../../docs/c1-agent-api.md)。

## 启动（无需模型密钥）

仓库根目录执行，Python 3.10+（本轮 Python 3.12）。使用原有依赖，不新增 Web/数据库服务：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-demo.txt
```

如果 `data/benchmark.sqlite` 不存在，在临时副本生成，避免重写仓库中的共享真值与已有运行数据：

```bash
fixture_dir=$(mktemp -d /tmp/futureledger-c1.XXXXXX)
cp -a scripts db data benchmark prototype "$fixture_dir/"
.venv/bin/python "$fixture_dir/scripts/generate_data.py"
cp "$fixture_dir/data/benchmark.sqlite" data/benchmark.sqlite
rm -rf "$fixture_dir"
```

显式开启开发认证并启动单进程。令牌只保存在当前 shell 环境；不要复制到文件、Git 或聊天。若切换终端，需要通过自己的安全配置重新提供同一个值。

```bash
export FUTURELEDGER_PROVIDER=local
export FUTURELEDGER_DEV_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m uvicorn services.api.dev:create_dev_app --factory --host 127.0.0.1 --port 8001
```

开发令牌只映射到合成 `test_user` 的两账户；不得将它用于部署。`services.api.app:app` 默认没有可用认证，所有业务接口拒绝访问；集成生产认证时实现 `Authenticator`，由认证层构造 Principal，禁止从用户请求体构造。

同一环境的另一个客户端进程可通过安全注入的同一环境变量调用；以下 Python 示例不打印凭证（在服务启动的同一 shell 或使用同样的安全变量绑定）：

```python
import os
import httpx

with httpx.Client(base_url="http://127.0.0.1:8001", trust_env=False,
                  headers={"Authorization": "Bearer " + os.environ["FUTURELEDGER_DEV_TOKEN"]}) as c:
    r = c.post("/api/sessions", json={"dataset_id": "personal_ledger"})
    r.raise_for_status()
    sid = r.json()["data"]["session_id"]
    r = c.post("/api/chat", json={"session_id": sid, "text": "从acc_daily给妈妈转25元，明天"})
    r.raise_for_status()
    proposal = r.json()["data"]
    assert proposal["status"] == "PROPOSED"
    assert proposal["execution_available"] is False
```

需要真实 LLM 时复用 README 中的 provider 环境变量，开发工厂使用 `ModelPlanner(ChatProvider())`；本轮没有真实模型准确率结果。默认 local 解析是有限规则，支持明确的元金额、ISO/年月日日期、今天/明天、账户 ID、当前目录收款人及代扣名称。未识别的参数必须补充，不能推断为已执行。

## API 与前端调用

所有业务接口用 `Authorization: Bearer ...`。正常与错误 JSON 均为 `{data,error,request_id}`；错误含 code/recoverable/next_action。框架校验错误不回显凭证或原始输入。`X-Request-ID` 可用于定位请求。OpenAPI 是 `/openapi.json`，交互定义由 FastAPI `/docs` 提供。

|接口|C1 行为|
|---|---|
|POST /api/sessions|新增：绑定认证主体、数据集和账户授权快照|
|POST /api/chat|四类结构化提议、业务校验、缺参/消歧、隔离上下文|
|POST /api/analysis|显式夹具适配器执行原静态契约的三指标单值查询；默认未接 D 返回 UNSUPPORTED|
|POST /api/plans/{id}/simulate|检查所有权/版本，再交 D；默认 UNSUPPORTED|
|POST /api/operations|检查提议、缺参、只读、保护、当前日期与目录，再交 B；默认 UNSUPPORTED|
|POST /api/operations/{id}/confirm|独立 B 确认协议；默认 UNSUPPORTED|
|POST /api/operations/{id}/verify|独立认证输入，绝不发送到模型；默认 UNSUPPORTED|
|POST /api/operations/{id}/execute|要求 Idempotency-Key，实际执行归 B；默认 UNSUPPORTED|
|GET /api/operations/{id}|B 权威状态协议；默认 UNSUPPORTED|
|POST /api/tasks/{id}/cancel|仅取消 C 内存中的未提交规划任务，不取消银行支付|
|GET /api/events|仅本人事件的有限 SSE 回放，Last-Event-ID 续接；非持久实时事件服务|

`POST /api/analysis` 示例：

```json
{"session_id":"<server session>","plan":{"kind":"analysis","dataset_id":"personal_ledger","semantic_version":1,"metric":"net_expense","start":"2026-09-01","end_exclusive":"2026-10-01","filters":{"category":"餐饮"},"group_by":null,"limit":100}}
```

此处 C1 计划以新的 `plan_id`、`plan_version=1` 保存；显式 DAG 和递增版本通过独立 C2 接口提供，持久化留给后续阶段。`context_ref` 用于显式补参：下一条 chat 可传 `context_refs:[...]`，或同一会话使用“还是上次那个”。新计划不继承银行授权；已取消历史不允许引用。约束累积后旧计划提交返回 PLAN_STALE。

同名张伟返回两个已验证候选：选择 rec_3 或 rec_4 后显式引用上一条 context_ref 继续。云盘默认受保护；“别动视频”等保护要求以及只读约束在会话内不能被模型撤销。C1 没有解除保护/授权执行的快捷聊天指令。

SSE 前端需用支持 Bearer header 的 fetch 流客户端；不要将 token 放进 URL。客户端保存每帧 id，断线用 Last-Event-ID 重连。进程重启后游标失效，不具备生产恢复保证。

## 验证与契约导出

```bash
FUTURELEDGER_PROVIDER=local .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_benchmark.py
node scripts/check_prototype.cjs
.venv/bin/python -m pip check
.venv/bin/python -m services.api.export_contracts --check
```

更新 C1 模型后运行 `.venv/bin/python -m services.api.export_contracts` 导出新版本契约，不能覆盖原 `analysis-plan.schema.json`/`semantic_catalog.json`。测试使用 Fake Provider 和 HTTP mock transport，结果仅表示确定性流程回归，不表示真实模型或银行验收。
