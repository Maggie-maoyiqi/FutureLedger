# FutureLedger · 未来账本

银行 AI Agent：用自然语言理解生活目标，读取授权账户和账单，预演未来现金流，再将用户明确选中的动作交给安全执行门，查询银行事实验证结果。

**当前阶段：开发前设计基线 + 合成数据 + 离线参考评测 + 可点击页面原型。完整银行 API、Agent、授权系统、Worker 和沙箱尚未实现。**

## 先读这些

|资料|解决的问题|
|---|---|
|[方案共识](docs/00-方案共识.md)|范围、两份设计的取舍、Text2SQL适用边界、统一口径|
|[技术架构](docs/01-技术架构.md)|分层、数据流、读写路径、状态机与恢复|
|[功能清单与页面](docs/02-功能清单与页面.md)|每模块功能、用户例子、七类页面|
|[数据库与接口](docs/03-数据库与接口契约.md)|表关系、金额与时间、语义指标、API|
|[数据与Benchmark](docs/04-test_user与Benchmark.md)|test_user、数据规模、预期答案、运行方法|
|[六人分工](docs/05-六人分工与里程碑.md)|A–F责任、接口、交叉验收、四个里程碑|
|[PostgreSQL DDL基线](db/schema.sql)|表、外键、索引与执行状态存储|
|[AnalysisPlan Schema](contracts/analysis-plan.schema.json)|受控分析意图及禁止额外参数|
|[原始参考方案](references/)|用户提供的两份DOCX，保持原样|

## 架构决策

Next.js / TypeScript → FastAPI / 单规划 Agent → 两条受控路径：

- **分析**：语义目录 → IntentPlan → 后端校验 → 参数化只读SQL → 精确数值和证据。模型不自由写SQL。
- **执行**：结构化操作 → 用户确认/必要强验证 → Execution Gate → PostgreSQL Outbox / Worker → Bank adapter → Mock Bank → 结果核验。

现金流使用整数分确定性引擎，独立于模型；银行事实与Agent任务状态隔离。首版不引入Redis、多Agent或向量库。详细架构包含后续实现目标，不代表当前已有完整服务。

## 立即运行离线验证

需要Python 3.10+；只使用标准库，无需API Key、数据库服务或pip依赖。Python测试已在3.14本机执行。

```bash
python3 scripts/generate_data.py
python3 scripts/generate_scenarios.py
python3 scripts/run_benchmark.py
python3 -m unittest discover -s tests -v
```

Windows PowerShell 使用 `py -3` 代替 `python3`。数据种子20261015，固定时钟2026-10-01（Asia/Shanghai）。每次生成会重建本地离线SQLite测试库，不触碰外部数据库。

- 标准流水3839条，test_user 3109条，隔离用户730条；原始导入3963条，含124重复行。
- 180条账单数值/凭据题，30条现金流题，60条待实现验收规格。
- `benchmark/VERIFICATION.md` 记录本次实际验证范围；写操作、安全及故障恢复规格保持NOT_RUN。
- `run_benchmark.py --predictions predictions.json` 校验团队系统的分析结果；缺题判失败。

## 看页面原型

```bash
python3 -m http.server 8765 --bind 127.0.0.1
```

打开 [本地原型](http://127.0.0.1:8765/prototype/)。查看七个页面，切换第1/13/19天购买、正常/延迟发薪日和日常预算。页面金额由整数分计算；账单汇总来自生成数据。原型无LLM，不连接银行，写按钮只展示流程说明。

可选Node.js运行原型逻辑检查：`node scripts/check_prototype.cjs`。这项是运行时smoke检查，不代替真实浏览器与移动端视觉验收。

## 六人分工起点

A：银行服务与数据；B：安全执行与Worker；C：Agent与应用API；D：账单/Text2SQL与现金流；E：前端交互；F：评测、沙箱、部署。每人写模块测试，F负责集成证据；具体成员姓名与时间安排由团队填入。

## 数据及实现边界

所有账号、身份、流水、授权、商户和故障均为虚构材料。没有真实银行服务、可用测试登录密码或固定万能验证码。

`db/schema.sql` 尚未在PostgreSQL执行验证；生产RLS/角色授权、账本平衡触发器、完整API/Worker/认证/沙箱均需后续实现。SQLite是离线参考数据集，不是Mock Bank。270条题目和规格不是270项已通过的产品测试。数据真值公开，适合回归，不能当隐藏评测结果。

竞赛规则、资格、人数限制、评分与截止日未在本次工作中核实。来源材料保留原作者内容；团队应另行决定项目许可证。不要提交真实账单、认证凭证或`.env`。
