# E+F 前端与交付状态

更新日期：2026-11-02。本文描述当前实现；历史分支的离线交互和失败统计不作为当前验收结果。

## 当前可运行能力

- `apps/web/` 使用 Vue 3、TypeScript、Vite、Vue Router、Pinia。必须通过同源 `/api/auth/*` 登录；账户、聊天、转账和规划使用 Demo API。
- 聊天入口为 `/api/ask`，不是 `/api/agent/chat`。转账必须独立密码复核、选择用途和最终确认，状态由后端返回。
- 账单问答支持受控查询、分组、趋势、明细及会话范围内的 CSV；最近交易包含普通收入，转账类型与用途类别分离。
- 聊天支持 30/90 天确定性合成情景及纯余额模拟，展示曲线、最低余额、日期、事件和假设。独立未来账本使用同一授权主账户当前余额。
- `/bills`、`/subscriptions`、`/tasks`、`/settings` 当前注册的是功能说明页。仓库内同名 Vue 文件和本地演示状态不代表这些产品能力已接通；偏好页没有实际生效的开发 Token 配置流程。
- README 默认后端入口仍为兼容 HTML；Vue 通过 Vite 访问。两入口均拦截聊天凭证、显示规划结果并使用授权余额。兼容页面加载时清理可识别的旧敏感聊天记录。

## 验证范围

- Python 完整测试、C4 离线门禁、前端组件/存储与兼容页面测试、Vue/TypeScript 检查、Vite 构建。
- Chromium 对 Vue 和兼容 HTML 的登录、聊天、转账、规划与凭证保护进行回归。具体新执行结果记录在 `checklist-recheck-fixes.md`。
- test-only local provider 和受控 provider 验证不代表真实 LLM 的自然语言准确率。

## 尚未完成的全系统验收

真实 A/B/D 服务联调、银行资金执行、完整代扣管理、任务中心与 SSE、生产强验证及恢复、角色隔离评审、移动端全面视觉回归均不因 Demo 检查通过而自动完成。现金流使用明确披露的固定合成事件，不是历史预测或真实到账保证。

## 复现命令

```bash
python -m unittest discover -s tests -v
python scripts/run_c4_quality.py --output /tmp/futureledger-c4-evidence
cd apps/web
npm ci
npm test
npm run check
npm run build
```

先按 README 安装 Python 依赖并生成/准备合成 fixture。所有资金操作测试仅针对合成数据。
