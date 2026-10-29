# FutureLedger · 未来账本

FutureLedger 是一个本地运行的个人账本问答演示。用户用自然语言提问，LLM 将问题分到查询、转账执行、规划或普通对话；查询再转换为受控 IntentPlan，由后端校验并编译只读 SQL。模型不直接编写或执行 SQL。

这个仓库目前提供合成账单、Text2SQL 聊天界面、本地演示转账和确定性的现金流模拟接口。它不会连接银行，也不适合存放真实账单或真实账户凭据。现金流模拟只使用经过校验的计划参数和合成场景，不会写入账本。

## 功能

- 左侧会话列表、右侧聊天消息流。
- 查询单值、类别／商户／账户汇总、排名、月度趋势、多类别折线、流水明细和平均每笔支出。
- 单值显示数字卡片，排名显示柱状图，时间趋势显示折线图，普通分组显示表格。
- 演示转账仅在两个合成用户之间进行；需重新输入 FutureLedger 密码，选择用途类别并确认。转出计入所选支出类别，转入计入收入。
- 查询结果不显示 IntentPlan、SQL 或内部流水 ID。
- 本地离线模式无需 API key；也支持 OpenAI、DeepSeek，以及支持 OpenAI-compatible Chat Completions JSON mode 的模型服务。

## 快速启动

需要 Python 3.10 或更新版本。先从 GitHub 仓库页面的 **Code** 菜单复制仓库地址，再在终端运行：

```bash
git clone https://github.com/Maggie-maoyiqi/FutureLedger
cd FutureLedger
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-demo.txt
python3 scripts/generate_data.py
python3 -m uvicorn demo.api:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。首次使用时选 **首次设置密码**，为演示用户 `test_user` 或 `isolation_user` 设置密码，然后登录。转账时会再次要求该密码。

如需结束服务，在运行 Uvicorn 的终端按 `Ctrl+C`。再次启动时执行最后一条 Uvicorn 命令即可。第一次生成数据之后，无需每次重新运行 `generate_data.py`；重新生成会覆盖合成基准数据。

## 配置模型 API

API key 和模型名称都通过环境变量提供。请在启动 Uvicorn 的同一个终端设置它们；不要把真实 key 写进 Python 文件、README、截图或 Git。`.env.example` 仅供参考，应用不会自动读取该文件。实际 `.env` 已列入 `.gitignore`。

**离线模式**无需设置 key，也不会调用 LLM。它使用有限的本地规则，只适合基本演示：

```bash
export FUTURELEDGER_PROVIDER=local
python3 -m uvicorn demo.api:app --host 127.0.0.1 --port 8000
```

**DeepSeek** 使用服务商控制台提供的 API key 和模型 ID：

```bash
export FUTURELEDGER_PROVIDER=deepseek
export FUTURELEDGER_API_KEY='YOUR_APIKEY'
export FUTURELEDGER_MODEL='YOUR_MODEL_NAME'
python3 -m uvicorn demo.api:app --host 127.0.0.1 --port 8000
```

把 `YOUR_APIKEY` 和 `YOUR_MODEL_NAME` 换成自己账户可用的值。DeepSeek 的默认 API Base URL 是 `https://api.deepseek.com`。

**OpenAI**：

```bash
export FUTURELEDGER_PROVIDER=openai
export FUTURELEDGER_API_KEY='YOUR_APIKEY'
export FUTURELEDGER_MODEL='YOUR_MODEL_NAME'
python3 -m uvicorn demo.api:app --host 127.0.0.1 --port 8000
```

**其他兼容 OpenAI Chat Completions 的模型服务**（例如提供兼容接口的国产模型服务）：

```bash
export FUTURELEDGER_PROVIDER=compatible
export FUTURELEDGER_API_KEY='YOUR_APIKEY'
export FUTURELEDGER_API_BASE='https://YOUR_PROVIDER_BASE_URL/v1'
export FUTURELEDGER_MODEL='YOUR_MODEL_NAME'
python3 -m uvicorn demo.api:app --host 127.0.0.1 --port 8000
```

`FUTURELEDGER_API_BASE` 填服务商给出的 API 根地址；程序会在末尾追加 `/chat/completions`。模型名称必须填写服务商提供的准确 model ID，代码不会猜测。兼容服务须支持 Chat Completions 格式及 JSON mode（`response_format: json_object`）。如果服务商文档提供不同的 API Base、模型名或 JSON mode 要求，请按其文档设置；不支持该协议的接口目前不能直接使用。

|变量|用途|
|---|---|
|`FUTURELEDGER_PROVIDER`|`local`、`deepseek`、`openai` 或 `compatible`|
|`FUTURELEDGER_API_KEY`|该模型服务的 API key|
|`FUTURELEDGER_MODEL`|服务商提供的模型 ID；启用 LLM 时必填|
|`FUTURELEDGER_API_BASE`|兼容服务必填；内置 DeepSeek/OpenAI 地址可覆盖|
|`FUTURELEDGER_TODAY`|可选固定日期，用于重复演示相对日期问题，例如 `2026-10-26`|

每个新终端都需要重新设置环境变量。也可在本机创建 `.env` 保存配置，但需自行通过 shell 加载；不要提交真实 `.env`。

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

规划路由示例：`我想存6000元买电脑，至少留3000元`。规划请求仍会先进入独立的 planning 路由；确定性模拟通过下面的接口执行。接口返回 30 天逐日余额、最低余额、最低发生日、可行性、事件、假设和冲突原因。

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
