# 协作约定

按 `docs/05-六人分工与里程碑.md` 认领A–F职责。新分支默认使用 `codex/模块名称`，main保持可复现。不要多人同时修改共享接口而不通知消费者。

提交前运行：

```bash
python3 scripts/generate_data.py
python3 scripts/generate_scenarios.py
python3 scripts/run_benchmark.py
python3 -m unittest discover -s tests -v
node scripts/check_prototype.cjs
```

更改种子、金额口径、时区、计划或API字段时，同步更新contracts、文档与真值；真值变更必须由代码以外的证据核查，不能只把失败测试改成新的实际输出。

PR写明具体问题、改变后的行为、测试证据和剩余限制。银行、安全、恢复实现必须检查银行最终事实，不能只检查聊天文案。新增测试成绩与现有离线参考成绩分组报告。

不得提交真实银行数据、认证密钥、验证码或个人密码。引用外部代码时记录来源与许可证；两份原设计保留来源，不将参考设计目标写成已完成成果。
