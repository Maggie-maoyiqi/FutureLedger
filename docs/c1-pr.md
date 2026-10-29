# PR title

feat(agent): add C1 intent planning and principal-scoped application API

# PR description

仓库此前只有 Demo 与离线参考计算，C 的会话、严格意图校验和应用接口没有独立实现。本 PR 新增 C1 FastAPI 服务：analysis/transfer/mandate/goal 四类提议，服务端缺参追问、收款人消歧、主体/数据集/账户隔离、显式上下文复用和不可被模型撤销的业务约束。比如“先不要转，只看看”始终只读；同名张伟必须选择；约束改变会使旧计划失效。

保留原 Demo、共享静态契约和业务真值。C1 分析夹具适配器遵循静态指标口径，排除转账；不沿用 Demo 将转账计入收支的扩展。B 执行门与 D 现金流只有类型化协议和返回 UNSUPPORTED 的适配器，不产生支付授权、伪造银行成功或现金流数值。新增 C1 OpenAPI/JSON Schema，开发认证需显式注入，无默认身份。

验证：全量 unittest 72/72（新增 C1 46 + 原有 26），离线 oracle 210/210，原型 7 路由/30 情景/延期交互、pip check、契约漂移和真实 HTTP 冒烟均通过。启动及执行命令见 `services/api/README.md`。测试包含 Fake Provider，不代表真实 LLM 准确率。原 60 条银行/恢复/沙箱验收仍 NOT_RUN。内存会话/SSE 仅单进程，生产认证、持久存储、DAG、重规划及真实 A/B/D/E 集成留给后续阶段。

评审重点：D 核对分析口径/规划参数，B 核对提议与执行边界，E 核对追问/消歧/错误/SSE 结构。没有破坏性修改 A/B/D/E 既有接口；新增扩展与六人/五人分工差异详见 `docs/c1-agent-api.md`。
