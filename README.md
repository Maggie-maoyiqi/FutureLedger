# FutureLedger · 未来账本

FutureLedger 是一个本地运行的个人银行与账本智能体演示。统一前端以银行总览为首页，并把 API 连接的聊天功能作为左侧导航中的 **智能体小智**。用户用自然语言提问时，LLM 将问题分到查询、转账执行、规划或无关问答；查询再转换为受控 IntentPlan，由后端校验并编译只读 SQL。模型不直接编写或执行 SQL。

这个仓库目前提供合成账单、基于 LLM 的 Text2SQL 聊天界面、本地演示转账和确定性的现金流模拟接口。启动应用必须配置 LLM provider、API key 和 model；应用不会自动退回 local offline mode。它不会连接真实银行，也不适合存放真实账单或真实账户凭据。

## 功能

- 一个由登录/首次设置密码统一保护的浏览器应用；登录后默认进入银行总览。
- 左侧保留原有仪表板目的地，并增加 **智能体小智**；右侧未来助手面板继续保留。
- 智能体小智页面包含会话列表、聊天消息流、查询结果卡、明细表和 CSV 导出。
- 查询单值、类别／商户／账户汇总、排名、月度趋势、多类别折线、流水明细和平均每笔支出。
- 单值显示数字卡片，排名显示柱状图，时间趋势显示折线图，普通分组显示表格。
- 演示转账仅在两个合成用户之间进行；需重新输入 FutureLedger 密码，选择用途类别并确认。转出计入所选支出类别，转入计入收入。
- 查询结果不显示 IntentPlan、SQL 或内部流水 ID。
- 必须使用已配置的 LLM；支持 OpenAI、DeepSeek，以及支持 OpenAI-compatible Chat Completions JSON mode 的模型服务。

完整的实际功能、示范问题、预期结果和回归命令见 [`docs/功能检验清单.md`](docs/功能检验清单.md)。

## 快速启动

需要 Python 3.10 或更新版本。先从 GitHub 仓库页面的 **Code** 菜单复制仓库地址，再在终端运行：

```bash
git clone https://github.com/Maggie-maoyiqi/FutureLedger
cd FutureLedger
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-demo.txt
python3 scripts/generate_data.py
export FUTURELEDGER_PROVIDER=deepseek  # 或 openai / compatible
export FUTURELEDGER_API_KEY='YOUR_APIKEY'
export FUTURELEDGER_MODEL='YOUR_MODEL_NAME'
python3 -m uvicorn --app-dir "$PWD" demo.api:create_app --factory --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。首次使用时选 **首次设置密码**，为演示用户 `test_user` 或 `isolation_user` 设置密码，然后登录；整个仪表板和智能体都会在登录后显示。转账时会再次要求该密码。

如需结束服务，在运行 Uvicorn 的终端按 `Ctrl+C`。再次启动时执行最后一条 Uvicorn 命令即可。第一次生成数据之后，无需每次重新运行 `generate_data.py`；重新生成会覆盖合成基准数据。

## Vue 3 工作台（Part A 前端）

`apps/web/` 是与现有 FastAPI Demo API 对接的 Vue 3 + TypeScript + Vite 工作台。先在一个终端启动上面的 FastAPI，再在另一个终端运行：

```bash
cd apps/web
npm ci
npm run dev
```

浏览器打开 Vite 显示的地址（通常为 `http://127.0.0.1:5173`）。Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8000`。Vue 工作台必须通过登录页进入；查询使用 `/api/ask`，转账使用后端的密码复核和用途确认接口。Vue 工作台不会把 mock 模式作为正式 API 的回退路径。

前端检查命令：

```bash
cd apps/web
npm run check
npm run build
```

## Role A 模拟银行

`services/mock-bank/` 提供独立的 FastAPI/PostgreSQL 模拟银行，覆盖账户、收款人、流水、报价、幂等转账、查证、平衡分录、代扣取消、复位和对账。它只使用合成数据；PostgreSQL 通过 Docker 运行，不需要安装到 `/Library/PostgreSQL/18`。本模块使用 Conda，完整命令、端口、环境变量及 B/C/F 交接见 [`services/mock-bank/README.md`](services/mock-bank/README.md)。

```bash
conda env create -f services/mock-bank/environment.yml
conda activate futureledger-bank
docker compose -f services/mock-bank/compose.bank.yml up -d postgres
export PYTHONPATH=services/mock-bank
export FUTURELEDGER_INTERNAL_TOKEN='replace-with-a-local-secret'
export FUTURELEDGER_ENV=development FUTURELEDGER_ALLOW_RESET=1
export BANK_DATABASE_URL='postgresql+psycopg://futureledger:futureledger@localhost:55432/futureledger_bank'
python -m mock_bank.seed reset
export BANK_DATABASE_URL='postgresql+psycopg://futureledger_runtime:futureledger-runtime@localhost:55432/futureledger_bank'
python -m uvicorn mock_bank.api:app --host 127.0.0.1 --port 8001
```

首个连接仅供迁移和复位；服务启动前切换到只能操作 `bank` schema 的本地 runtime 账号。以上密码仅属于可丢弃的合成 Compose 环境。

## 配置模型 API

API key 和模型名称都通过环境变量提供。请在启动 Uvicorn 的同一个终端设置它们；不要把真实 key 写进 Python 文件、README、截图或 Git。`.env.example` 仅供参考，应用不会自动读取该文件。实际 `.env` 已列入 `.gitignore`。

**DeepSeek** 使用服务商控制台提供的 API key 和模型 ID：

```bash
export FUTURELEDGER_PROVIDER=deepseek
export FUTURELEDGER_API_KEY='YOUR_APIKEY'
export FUTURELEDGER_MODEL='YOUR_MODEL_NAME'
python3 -m uvicorn --app-dir "$PWD" demo.api:create_app --factory --host 127.0.0.1 --port 8000
```

把 `YOUR_APIKEY` 和 `YOUR_MODEL_NAME` 换成自己账户可用的值。DeepSeek 的默认 API Base URL 是 `https://api.deepseek.com`。

**OpenAI**：

```bash
export FUTURELEDGER_PROVIDER=openai
export FUTURELEDGER_API_KEY='YOUR_APIKEY'
export FUTURELEDGER_MODEL='YOUR_MODEL_NAME'
python3 -m uvicorn --app-dir "$PWD" demo.api:create_app --factory --host 127.0.0.1 --port 8000
```

**其他兼容 OpenAI Chat Completions 的模型服务**（例如提供兼容接口的国产模型服务）：

```bash
export FUTURELEDGER_PROVIDER=compatible
export FUTURELEDGER_API_KEY='YOUR_APIKEY'
export FUTURELEDGER_API_BASE='https://YOUR_PROVIDER_BASE_URL/v1'
export FUTURELEDGER_MODEL='YOUR_MODEL_NAME'
python3 -m uvicorn --app-dir "$PWD" demo.api:create_app --factory --host 127.0.0.1 --port 8000
```

`FUTURELEDGER_API_BASE` 填服务商给出的 API 根地址；程序会在末尾追加 `/chat/completions`。模型名称必须填写服务商提供的准确 model ID，代码不会猜测。兼容服务须支持 Chat Completions 格式及 JSON mode（`response_format: json_object`）。如果服务商文档提供不同的 API Base、模型名或 JSON mode 要求，请按其文档设置；不支持该协议的接口目前不能直接使用。

|变量|用途|
|---|---|
|`FUTURELEDGER_PROVIDER`|`deepseek`、`openai` 或 `compatible`；必须设置|
|`FUTURELEDGER_API_KEY`|该模型服务的 API key|
|`FUTURELEDGER_MODEL`|服务商提供的模型 ID；启用 LLM 时必填|
|`FUTURELEDGER_API_BASE`|兼容服务必填；内置 DeepSeek/OpenAI 地址可覆盖|
|`FUTURELEDGER_TODAY`|可选固定日期，用于重复演示相对日期问题，例如 `2026-10-26`|

每个新终端都需要重新设置环境变量。也可在本机创建 `.env` 保存配置，但需自行通过 shell 加载；不要提交真实 `.env`。未设置 provider、API key 或 model 时，应用会在启动阶段直接报错。

## 可用于演示的问题

以下账单问题会交给查询路由。月份未写年份时，LLM 会结合当前日期理解；合成数据覆盖的日期有限，因此查询结果取决于数据集内容。

- `这个月的支出是多少？`
- `上个月的收入明细`
- `8月和9月的各项净支出`
- `2026年9月各类别净支出排名前五`
- `最近三个月每月餐饮支出趋势`
- `本月转账记录`
- `上个月交通平均每笔支出是多少？`

执行路由示例：`给测试用户B转25元`。系统会显示转账预览，然后要求重新验证密码、选择类别并最终确认；不要把密码发在聊天框里。

规划路由示例：`我想存6000元买电脑，至少留3000元`。规划请求进入独立的 planning 路由，提取并校验目标与底线后返回合成情景比较卡；缺参时会追问。也可通过下面的接口直接执行确定性模拟。接口返回 30 天逐日余额、最低余额、最低发生日、可行性、事件、假设和冲突原因。

### 现金流模拟接口

`POST /api/plans/{plan_id}/simulate` 需要当前登录会话创建的 `session_id`，请求体示例：

```json
{
  "session_id": "<session>",
  "plan_version": 1,
  "salary_day": 17,
  "purchase_day": 19,
  "daily_budget_cents": 8000,
  "floor_cents": 300000,
  "goal_cents": 600000
}
```

金额统一使用整数分。接口只读取会话身份和计划参数，使用固定的合成场景（日 5 房租、日 8 云盘、日 10 视频、选定日期工资和电脑目标支出），不会修改 SQLite 账户或流水。无效会话返回 `403`；计划参数校验失败返回 `422`。

## 数据和隐私范围

仓库中的账单和用户均为合成数据。查询计划阶段只向模型提供当前登录用户范围内的有限上下文（账户、商户及最多 30 条已入账流水样例）；不发送其他用户、自由备注或完整历史。数据库执行结果、完整查询结果和 SQL 不会发回模型。路由阶段只收到当前问题。执行路由不会让模型执行写操作，转账需走独立的密码复核和确认流程。

登录、会话与待处理动作仅用于本地演示，不是生产身份认证。重启服务会清空内存中的登录会话。演示转账仅修改本地合成数据副本，不会修改基准账本或连接银行。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

也可运行离线参考评测：

```bash
python3 scripts/generate_data.py
python3 scripts/generate_scenarios.py
python3 scripts/run_benchmark.py
```

这些检查不需要模型 API key。生产身份认证、真实银行服务和部署沙箱尚未实现；现金流接口是本地合成数据演示，不代表真实账户可用余额。

## B 部分：安全执行门

`services/api/execution/` 和 `services/worker/` 提供与 Agent 解耦的写操作执行门。它接收上游规划器生成的操作参数，但风险级别由服务端按日累计额度重新计算，不能由模型或浏览器授予。

执行顺序是：创建操作快照 → 用户确认参数和计划版本 → 红色操作独立强验证 → 原子预占日额度 → 写入 Outbox → Worker 通过 `BankAdapter` 提交 → 查询最终银行状态。密码不会写入操作参数、Outbox 或审计摘要。

本地模块可以直接运行 B 的验收测试：

```bash
python3 -m unittest discover -s tests -p 'test_execution_gate.py' -v
```

测试覆盖确认过期、参数绑定、重复幂等、两笔 600 元并发额度竞态，以及银行成功但响应丢失后的 `PENDING_UNKNOWN` 查证。`services/api/execution/api.py` 提供可挂载到 FastAPI 的 `/api/operations` 工厂；若使用 SQLite，连接应通过 `ExecutionService.connect()` 打开。

B 的接口和未覆盖边界记录在 [执行门实现计划](docs/plans/2026-11-01-execution-gate.md)。当前内置的 `InMemoryBank` 只用于本地测试，不是生产银行连接。
模拟银行的 pytest、PostgreSQL 对账和 A-owned 验收报告使用单独命令，见模块 README。共享 transfer/mandate 用例的 A 侧证据标记为 `A_SIDE_PASS`，仍需 B/C 的执行、追问或同意边界证据才能形成全链路通过。
