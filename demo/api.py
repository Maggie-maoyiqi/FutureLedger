"""Local API for routed ledger queries and confirmed synthetic actions."""

import csv
import hashlib
import hmac
import io
import os
import secrets
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .planner import classify_route, make_intent, parse_transfer, PLANNING_REQUEST
from .safety import blocked_question, query_followup, CREDENTIAL
from .planning import build_chat_plan
from .explain import compare_period
from .provider import ChatProvider, ProviderError
from .cashflow import simulate_cashflow
from .query import Ledger, QueryError, ACCOUNT_NAMES
from .semantic import GROUPS, METRICS, IntentPlan, public_catalog

ROOT = Path(__file__).resolve().parents[1]


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auth_token: str
    dataset_id: str = "personal_ledger"


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auth_token: str


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    question: str = Field(min_length=1, max_length=500)


class PlanSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(min_length=1, max_length=128)
    plan_version: int = Field(ge=1)
    salary_day: int = Field(ge=1, le=30)
    purchase_day: int = Field(ge=1, le=30)
    daily_budget_cents: int = Field(default=8000, ge=0)
    floor_cents: int = Field(default=300000, ge=0)
    goal_cents: int = Field(default=600000, ge=0)


class ActionAuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    password: SecretStr


class ActionConfirmCategoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    authorization_id: str
    category: str = Field(min_length=1, max_length=20)


def password_hash(password: str):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str):
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def display_value(value, metric):
    if value is None:
        return "没有可计算的消费记录"
    if metric in {"transaction_count", "transfer_count", "activity_count"}:
        return f"{int(value)} 笔"
    return f"¥{value / 100:,.2f}"


def chart_for(plan: IntentPlan, question: str):
    if plan.kind == "detail":
        return "table"
    if plan.group_by is None:
        return "metric_card"
    if plan.group_by == "month":
        return "line"
    if re.search(r"排名|排行|前\s*[一二三四五六七八九十\d]+|\btop\b|比较|对比", question, re.I):
        return "bar"
    return "table"


def csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if isinstance(value, str) and text.startswith(("=", "+", "-", "@")) else text


def requested_balance_account(question: str):
    """Return (is account snapshot question, optional requested account id)."""
    q = question.strip().lower()
    account_words = re.search(r"账户|银行卡|余额|储蓄|存款|活期|account|balance|savings", q, re.I)
    fact_words = re.search(r"余额|多少钱|多少余额|剩(?:下)?多少|还有多少|金额|哪些账户|账户列表|几(?:个|张)(?:银行)?账户|多少个(?:银行)?账户|账户信息|查看.{0,4}账户|balance", q, re.I)
    planning_words = re.search(r"目标|规划|计划|以后|未来|想存|要存|攒|买", q)
    transaction_words = re.search(r"支出|收入|流水|交易|消费|退款|转|汇款|付款|支付|扣款|收款", q)
    historical_words = re.search(
        r"上(?:个|一)(?:月|周|季度|年)|过去|历史|之前|截至|昨天|前天|去年|前年|"
        r"\d{4}\s*年|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号]|\s*(?:初|中|末|底))",
        q,
    )
    if not account_words or not fact_words or planning_words or transaction_words or historical_words:
        return False, None
    wants_savings = re.search(r"储蓄|存款|savings|acc_savings", q, re.I)
    wants_daily = re.search(r"主账户|日常账户|活期|acc_daily", q, re.I)
    # A balance shortcut may only broaden to all accounts when no named
    # account was requested. Unknown names must not silently lose their scope.
    generic_words = (
        'acc_savings', 'acc_daily', 'acc_other', '储蓄账户', '主账户', '日常账户',
        '演示账户', '储蓄', '存款', '活期', 'savings', 'accounts', 'account',
        'balance', 'total', 'list', 'how much', 'my', 'show', 'what is', 'me',
        '主', '日常', '演示', '能告诉', '告诉', '能否', '可以', '麻烦', '显示',
        '多少钱', '多少余额', '多少个', '几个', '哪些', '各自', '分别', '银行账户',
        '账户列表', '账户信息', '银行卡', '账户', '总余额', '余额', '金额',
        '剩下多少钱', '剩多少钱', '还有多少钱', '剩下多少', '剩多少', '还有多少', '是多少', '有多少', '请问',
        '帮我', '查询', '查看', '我的', '银行', '列表', '信息', '总共',
        '合计', '全部', '所有', '当前', '现在', '多少', '请', '我', '有',
        '和', '的', '是', '呢', '吗', '张', '个',
    )
    mentions = re.findall(r'([a-z0-9\u4e00-\u9fff_ ]*?)(?:账户|银行卡|卡|余额|accounts?\b|balance\b)', q)
    for remainder in mentions:
        for word in sorted(generic_words, key=len, reverse=True):
            remainder = remainder.replace(word, '')
        if re.search(r'[a-z0-9\u4e00-\u9fff_]', remainder):
            return True, '__unknown_account__'
    if 'acc_other' in q or '演示账户' in q:
        return True, 'acc_other'
    if wants_savings and wants_daily:
        return True, None
    if wants_savings:
        return True, "acc_savings"
    if wants_daily:
        return True, "acc_daily"
    return True, None


def account_snapshot_payload(ledger: Ledger, user: str, account_id: str | None = None):
    rows = ledger.account_snapshot(user)
    if account_id is not None:
        rows = [row for row in rows if row["account_id"] == account_id]
    if not rows:
        raise QueryError("ACCOUNT_NOT_FOUND", "当前登录用户没有这个账户")
    if len(rows) == 1:
        answer = f"{rows[0]['account_name']}当前余额为 ¥{rows[0]['balance_cents'] / 100:,.2f}。"
    else:
        total = sum(row["balance_cents"] for row in rows)
        details = "，".join(f"{row['account_name']} ¥{row['balance_cents'] / 100:,.2f}" for row in rows)
        answer = f"你的账户总余额为 ¥{total / 100:,.2f}（{details}）。"
    return rows, answer


def simulate_plan_payload(plan_id: str, request: PlanSimulationRequest, *, user_id: str,
                          opening_balance_cents: int = 900000) -> dict[str, object]:
    """Build a deterministic simulation response without changing ledger state."""
    result = simulate_cashflow(
        opening_balance_cents=opening_balance_cents,
        salary_day=request.salary_day,
        purchase_day=request.purchase_day,
        daily_budget_cents=request.daily_budget_cents,
        floor_cents=request.floor_cents,
        goal_cents=request.goal_cents,
    )
    return {
        "status": "simulated",
        "route": "planning",
        "plan_id": plan_id,
        "plan_version": request.plan_version,
        "result": result.to_dict(),
    }


def create_app(*, db_path=None, provider=None, today=None):
    app = FastAPI(title="FutureLedger Text2SQL demo", version="0.2.0")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        is_simulation = request.url.path.startswith("/api/plans/") and request.url.path.endswith("/simulate")
        detail = {
            "code": "INVALID_SIMULATION" if is_simulation else "VALIDATION_ERROR",
            "message": "计划模拟参数无效" if is_simulation else "请求参数无效",
            "errors": [
                {"loc": list(error.get("loc", ())), "type": error.get("type"), "msg": error.get("msg")}
                for error in exc.errors()
            ],
        }
        return JSONResponse(status_code=422, content={"detail": detail})

    source_path = Path(db_path or ROOT / "data/benchmark.sqlite").resolve()
    runtime_path = source_path.with_name(source_path.stem + "_runtime.sqlite")
    if source_path.is_file() and not runtime_path.exists():
        shutil.copy2(source_path, runtime_path)
    ledger = Ledger(runtime_path)
    if runtime_path.is_file():
        import sqlite3
        with sqlite3.connect(runtime_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS demo_transfers (action_id TEXT PRIMARY KEY, sender_user_id TEXT NOT NULL, recipient_user_id TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)")
    model = provider or ChatProvider()
    clock = today or date.fromisoformat(os.getenv("FUTURELEDGER_TODAY", date.today().isoformat()))
    sessions: dict[str, str] = {}
    auth_tokens: dict[str, str] = {}
    pending: dict[str, str] = {}
    planning_context: dict[str, dict] = {}
    planning_pending: set[str] = set()
    actions: dict[str, dict] = {}
    action_authorizations: dict[str, dict] = {}
    queries: dict[str, dict] = {}

    @app.get("/")
    def home():
        return FileResponse(ROOT / "demo/web/index.html")

    @app.get("/api/health")
    def health():
        return {"status": "ready" if ledger.path.is_file() else "fixture_missing", "provider": model.provider,
                "dataset": "personal_ledger", "date": clock.isoformat(), "query_read_only": True,
                "execution": "synthetic_confirmation_required"}

    @app.get("/api/semantic")
    def semantic():
        return public_catalog()

    @app.post("/api/auth/register")
    def register(request: CredentialsRequest):
        username = request.username.strip()
        password = request.password.get_secret_value()
        if len(password) < 12 or len(password) > 128:
            raise HTTPException(422, {"code": "PASSWORD_LENGTH", "message": "密码长度需为 12–128 个字符。"})
        if username not in {"test_user", "isolation_user"}:
            raise HTTPException(400, {"code": "UNKNOWN_DEMO_USER", "message": "本地演示仅允许为已有合成用户创建登录。"})
        if not ledger.path.is_file():
            raise HTTPException(503, {"code": "FIXTURE_MISSING", "message": "先运行 python3 scripts/generate_data.py"})
        import sqlite3
        try:
            with sqlite3.connect(runtime_path) as conn:
                conn.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                             (username, username, password_hash(password), datetime.now().astimezone().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, {"code": "USER_EXISTS", "message": "这个演示账号已设置密码，请直接登录。"}) from exc
        return {"status": "registered", "username": username}

    @app.post("/api/auth/login")
    def login(request: CredentialsRequest):
        username = request.username.strip()
        password = request.password.get_secret_value()
        if len(password) > 128:
            raise HTTPException(401, {"code": "INVALID_CREDENTIALS", "message": "用户名或密码不正确。"})
        if not ledger.path.is_file():
            raise HTTPException(503, {"code": "FIXTURE_MISSING", "message": "先运行 python3 scripts/generate_data.py"})
        import sqlite3
        with sqlite3.connect(runtime_path) as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
        if row is None or not verify_password(password, row[0]):
            raise HTTPException(401, {"code": "INVALID_CREDENTIALS", "message": "用户名或密码不正确。"})
        token = secrets.token_urlsafe(32)
        auth_tokens[token] = username
        return {"status": "authenticated", "auth_token": token, "username": username}

    @app.post("/api/auth/logout")
    def logout(request: LogoutRequest):
        username = auth_tokens.pop(request.auth_token, None)
        if username:
            for token, user in list(sessions.items()):
                if user == username:
                    sessions.pop(token, None)
        return {"status": "logged_out"}

    @app.post("/api/sessions")
    def new_session(request: SessionRequest):
        if request.dataset_id != "personal_ledger":
            raise HTTPException(400, {"code": "UNKNOWN_DATASET", "message": "仅提供 personal_ledger 演示数据集"})
        user = auth_tokens.get(request.auth_token)
        if user is None:
            raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "请先登录演示账号。"})
        if not ledger.path.is_file():
            raise HTTPException(503, {"code": "FIXTURE_MISSING", "message": "先运行 python3 scripts/generate_data.py"})
        token = secrets.token_urlsafe(24)
        sessions[token] = user
        return {"session_id": token, "dataset_id": "personal_ledger", "semantic_version": 1,
                "demo_user": user, "warning": "本地演示身份，不是生产鉴权"}

    @app.post("/api/plans/{plan_id}/simulate")
    def simulate_plan(plan_id: str, request: PlanSimulationRequest):
        user = sessions.get(request.session_id)
        if user is None:
            raise HTTPException(403, {"code": "FORBIDDEN", "message": "无效或已失效的演示会话"})
        try:
            accounts = sorted(ledger.account_snapshot(user), key=lambda row: row['account_id'])
            if not accounts:
                raise QueryError('ACCOUNT_NOT_FOUND', '当前用户没有可用于模拟的账户。')
            payload = simulate_plan_payload(plan_id, request, user_id=user,
                opening_balance_cents=accounts[0]['balance_cents'])
            payload['assumptions'] = [
                f"使用当前授权主账户（{accounts[0]['account_name']}）余额，未合并其他账户。",
                '固定合成事件：房租3500元、云存储30元、视频订阅68元、工资8000元；不是历史预测。',
                '金额、日期和预算仅用于情景模拟，不执行转账。',
            ]
            return payload
        except QueryError as exc:
            raise HTTPException(422, {'code': exc.code, 'message': str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(422, {"code": "INVALID_SIMULATION", "message": str(exc)}) from exc

    @app.get("/api/accounts")
    def accounts_snapshot(session_id: str = Query(...)):
        user = sessions.get(session_id)
        if user is None:
            raise HTTPException(403, {"code": "FORBIDDEN", "message": "无效或已失效的演示会话"})
        try:
            rows, _ = account_snapshot_payload(ledger, user)
        except QueryError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc
        return {"status": "ready", "accounts": rows,
                "total_balance_cents": sum(row["balance_cents"] for row in rows)}

    @app.post("/api/ask")
    async def ask(request: AskRequest):
        user = sessions.get(request.session_id)
        if user is None:
            raise HTTPException(403, {"code": "FORBIDDEN", "message": "无效或已失效的演示会话"})
        try:
            blocked = blocked_question(request.question, user, ledger.accounts(user))
        except QueryError as exc:
            raise HTTPException(503, {'code': exc.code, 'message': str(exc)}) from exc
        if blocked:
            return {'status': 'blocked', 'code': blocked[0], 'message': blocked[1]}
        previous = pending.get(request.session_id)
        effective_question = query_followup(previous, request.question)
        planning_followup = (request.session_id in planning_pending
            and bool(re.match(r'^(?:目标|至少|保留|最低|余额|每天|每日|工资|改为|改成|30天|三个月|\d+\s*(?:元|天)|主账户|用主账户)', request.question))
            and not bool(re.search(r'支出|收入|退款|账单|明细|交易|笔数', request.question)))
        if re.search(r'重新规划|新目标|重置方案|从头规划', request.question):
            planning_context.pop(request.session_id, None)
        is_balance_question, requested_account_id = requested_balance_account(request.question)
        if is_balance_question and not PLANNING_REQUEST.search(request.question) and not planning_followup:
            try:
                rows, answer = account_snapshot_payload(ledger, user, requested_account_id)
            except QueryError as exc:
                return {"status": "clarification", "route": "query", "code": exc.code, "message": str(exc)}
            query_id = secrets.token_urlsafe(18)
            rows = [{key: value for key, value in row.items() if key != 'account_id'} for row in rows]
            pending.pop(request.session_id, None)
            if re.search(r'几(?:个|张).*账户|多少个.*账户', request.question):
                answer = f'你有 {len(rows)} 个授权账户。'
            queries[query_id] = {"session_id": request.session_id, "rows": rows}
            return {"status": "answer", "route": "query", "answer": answer, "rows": rows,
                    "chart": "metric_card" if len(rows) == 1 else "table", "value_format": "money",
                    "query_id": query_id}
        try:
            decision = await classify_route(effective_question, model,
                recent_context={'pending_query': previous} if previous and effective_question != request.question else None)
        except ProviderError as exc:
            raise HTTPException(502, {"code": "PROVIDER_ERROR", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(502, {"code": "INVALID_ROUTE", "message": "模型路由结果无效，请重试。"}) from exc
        route, route_message = decision["route"], decision.get("message")
        if planning_followup and route in {'view', 'clarification', 'query'}:
            route = 'planning'
        if route != 'planning':
            planning_pending.discard(request.session_id)
        if route == "planning":
            pending.pop(request.session_id, None)
            try:
                payload = await build_chat_plan(request.question, ledger, user, model,
                    previous=planning_context.get(request.session_id))
            except ProviderError as exc:
                raise HTTPException(502, {'code': 'PROVIDER_ERROR', 'message': str(exc)}) from exc
            context = payload.pop('_planning_context', {})
            planning_context[request.session_id] = context
            if payload['status'] == 'clarification':
                planning_pending.add(request.session_id)
            else:
                planning_pending.discard(request.session_id)
            return payload
        if route == "execution":
            if "取消代扣" in request.question or "取消订阅" in request.question:
                return {"status": "unsupported", "route": "execution", "message": "此演示版只实现双方账户间的转账确认；取消代扣暂不执行。"}
            parsed, clarification = parse_transfer(request.question)
            if clarification:
                return {"status": "clarification", "route": "execution", "message": clarification}
            recipient = parsed["recipient_user"]
            if recipient == user:
                return {"status": "clarification", "route": "execution", "message": "收款方不能是当前演示用户本人；请选择另一位演示用户。"}
            action_id = secrets.token_urlsafe(18)
            actions[action_id] = {"session_id": request.session_id, "sender_user": user,
                                  "recipient_user": recipient, "amount_cents": parsed["amount_cents"],
                                  "status": "PENDING"}
            labels = {"test_user": "测试用户A", "isolation_user": "测试用户B"}
            amount = parsed["amount_cents"] / 100
            return {"status": "confirmation_required", "route": "execution", "action_id": action_id,
                    "message": f"请确认演示转账：从{labels[user]}转出 ¥{amount:,.2f}，转入{labels[recipient]}。确认后会同时更新双方模拟账户余额和流水。"}
        if route != "query":
            pending.pop(request.session_id, None)
            return {"status": route, "route": route, "message": route_message or "请重新描述。"}

        try:
            accounts = ledger.accounts(user)
            merchants = ledger.merchants(user, accounts)
            user_context = ledger.llm_context(user, accounts, limit=30)
            if CREDENTIAL.search(json.dumps({'context': user_context, 'merchants': merchants[:160]}, ensure_ascii=False)):
                return {'status': 'blocked', 'code': 'SENSITIVE_CONTEXT', 'message': '账本上下文包含疑似凭证，请先清理后再查询。'}
            intent = await make_intent(effective_question, clock, merchants, user_context, model)
        except ProviderError as exc:
            raise HTTPException(502, {"code": "PROVIDER_ERROR", "message": str(exc)}) from exc
        except QueryError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from exc

        if not isinstance(intent, dict) or intent.get("route") not in {"analysis", "clarification"}:
            return {"status": "clarification", "route": "query", "message": "无法可靠识别查询，请换一种说法。"}
        route = intent["route"]
        if route != "analysis":
            if route == "clarification":
                pending[request.session_id] = effective_question[:500]
            else:
                pending.pop(request.session_id, None)
            return {"status": route, "route": "query", "message": str(intent.get("message", "请重新表述。"))[:300]}
        if set(intent) != {"route", "plan"}:
            return {"status": "clarification", "route": "query", "message": "查询计划包含未批准的内容，请重新表述。"}
        try:
            plan = IntentPlan.model_validate_json(json.dumps(intent["plan"], ensure_ascii=False))
            executed = ledger.execute(plan, user)
            comparison = compare_period(plan, ledger, user) if re.search(r'为什么|原因|为何', request.question) else None
            if comparison:
                executed['rows'] = comparison['rows']
                plan = plan.model_copy(update={'group_by': 'category'})
        except ValidationError:
            return {"status": "clarification", "route": "query", "message": "查询条件、时间或指标不完整，请明确后重试。"}
        except QueryError as exc:
            status = "clarification" if exc.code in {"UNKNOWN_ENUM", "UNKNOWN_ENTITY"} else "blocked"
            return {"status": status, "route": "query", "code": exc.code, "message": str(exc)}

        rows = executed["rows"]
        for row in rows:
            if plan.group_by == 'account':
                row['group_key'] = ACCOUNT_NAMES.get(row['group_key'], '银行账户')
            if plan.series_by == 'account':
                row['series_key'] = ACCOUNT_NAMES.get(row['series_key'], '银行账户')
        pending.pop(request.session_id, None)
        if comparison:
            answer = comparison['answer']
        elif plan.kind == "detail":
            answer = f"找到并展示 {len(rows)} 条符合条件的已入账流水。"
        elif plan.group_by:
            if plan.series_by:
                answer = f"{METRICS[plan.metric]} · 按月趋势（按{GROUPS[plan.series_by]}分线）"
            elif plan.group_by == "month":
                answer = f"{METRICS[plan.metric]} · 按月趋势"
            else:
                answer = f"{METRICS[plan.metric]} · 按{GROUPS[plan.group_by]}整理"
        else:
            value = rows[0]["value_cents"]
            answer = display_value(value, plan.metric)
        query_id = secrets.token_urlsafe(18)
        queries[query_id] = {"session_id": request.session_id, "rows": rows,
                           'group_label': GROUPS.get(plan.group_by, '项目'),
                           'series_label': GROUPS.get(plan.series_by, '系列'),
                           'value_format': 'count' if plan.metric in {'transaction_count', 'transfer_count', 'activity_count'} else 'money'}
        return {
            "status": "answer", "route": "query", "answer": answer, "rows": rows,
            "chart": 'bar' if comparison else chart_for(plan, effective_question),
            "value_format": "count" if plan.metric in {"transaction_count", "transfer_count", "activity_count"} else "money",
            "query_id": query_id,
            'group_label': GROUPS.get(plan.group_by, '项目'),
            'series_label': GROUPS.get(plan.series_by, '系列'),
        }

    @app.post("/api/actions/{action_id}/authorize")
    def authorize_action(action_id: str, request: ActionAuthorizeRequest):
        user = sessions.get(request.session_id)
        action = actions.get(action_id)
        if user is None or action is None or action["session_id"] != request.session_id:
            raise HTTPException(404, {"code": "ACTION_NOT_FOUND", "message": "这项待确认操作不存在或已过期"})
        import sqlite3
        with sqlite3.connect(runtime_path) as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE username=?", (user,)).fetchone()
        if row is None or not verify_password(request.password.get_secret_value(), row[0]):
            raise HTTPException(401, {"code": "INVALID_CREDENTIALS", "message": "登录密码不正确，转账未执行。"})
        if action["status"] != "PENDING":
            raise HTTPException(409, {"code": "ACTION_NOT_PENDING", "message": "这笔操作已处理或已失效。"})
        authorization_id = secrets.token_urlsafe(24)
        action_authorizations[authorization_id] = {"action_id": action_id, "session_id": request.session_id,
                                                   "expires_at": datetime.now().timestamp() + 120}
        return {"status": "authorized", "authorization_id": authorization_id,
                "categories": ["餐饮", "交通", "购物", "娱乐", "订阅", "房租", "医疗", "其他"]}

    @app.post("/api/actions/{action_id}/confirm")
    def confirm_action(action_id: str, request: ActionConfirmCategoryRequest):
        user = sessions.get(request.session_id)
        action = actions.get(action_id)
        authorization = action_authorizations.get(request.authorization_id)
        if user is None or action is None or action["session_id"] != request.session_id:
            raise HTTPException(404, {"code": "ACTION_NOT_FOUND", "message": "这项待确认操作不存在或已过期"})
        if action['status'] == 'SUCCEEDED':
            if (action.get('completed_authorization_id') == request.authorization_id
                    and action.get('category') == request.category):
                return {'status': 'transfer_complete', 'route': 'execution', 'message': '这笔演示转账已经完成，不会重复执行。'}
            raise HTTPException(409, {'code': 'ACTION_ALREADY_COMPLETED', 'message': '操作已完成，不能修改参数或再次扣款。'})
        if (authorization is None or authorization["action_id"] != action_id or
                authorization["session_id"] != request.session_id or authorization["expires_at"] < datetime.now().timestamp()):
            action_authorizations.pop(request.authorization_id, None)
            raise HTTPException(401, {"code": "AUTHORIZATION_EXPIRED", "message": "密码授权已过期，请重新验证。"})
        if request.category not in {"餐饮", "交通", "购物", "娱乐", "订阅", "房租", "医疗", "其他"}:
            raise HTTPException(422, {"code": "UNKNOWN_CATEGORY", "message": "请选择有效的转账用途类别。"})
        if action["status"] == "SUCCEEDED":
            return {"status": "transfer_complete", "route": "execution", "message": "这笔演示转账已经完成，不会重复执行。"}
        try:
            result = ledger.execute_demo_transfer(
                action_id, action["sender_user"], action["recipient_user"], action["amount_cents"], request.category,
                posted_at=f"{clock.isoformat()}T12:00:00+08:00",
            )
        except QueryError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
        action["status"] = result["status"]
        action['completed_authorization_id'] = request.authorization_id
        action['category'] = request.category
        action_authorizations.pop(request.authorization_id, None)
        if action["status"] == "SUCCEEDED":
            labels = {"test_user": "测试用户A", "isolation_user": "测试用户B"}
            amount = action["amount_cents"] / 100
            return {"status": "transfer_complete", "route": "execution",
                    "message": f"演示转账已完成：¥{amount:,.2f} 已从{labels[action['sender_user']]}转入{labels[action['recipient_user']]}；已将转出记为“{request.category}”支出、转入记为收入，双方余额和流水已更新。"}
        return {"status": "transfer_complete", "route": "execution", "message": "这笔演示转账已经完成，不会重复执行。"}

    @app.get("/api/queries/{query_id}/export.csv")
    def export_csv(query_id: str, session_id: str = Query(...)):
        if session_id not in sessions:
            raise HTTPException(403, {"code": "FORBIDDEN", "message": "无效会话"})
        item = queries.get(query_id)
        if item is None or item["session_id"] != session_id:
            raise HTTPException(404, {"code": "NOT_FOUND", "message": "没有这条查询或无权导出"})
        rows = item["rows"]
        fields = list(rows[0]) if rows else ["value_cents"]
        output = io.StringIO()
        writer = csv.writer(output)
        labels = {'posted_at': '入账时间', 'category': '类别', 'merchant': '商户', 'kind': '类型',
                  'direction': '方向', 'amount_cents': '金额（分）', 'balance_cents': '余额（分）',
                  'account_name': '账户', 'group_key': item.get('group_label', '项目'),
                  'series_key': item.get('series_label', '系列'),
                  'value_cents': '笔数' if item.get('value_format') == 'count' else '金额（分）'}
        writer.writerow([labels.get(field, field) for field in fields])
        for row in rows:
            writer.writerow([csv_safe(row.get(field)) for field in fields])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="futureledger-{query_id}.csv"'})

    return app


# The production entry point is the factory, so configuration is validated at
# Uvicorn startup after the user exports an LLM provider, key and model.
# Tests and embedded callers should pass an explicit provider to create_app().
app = None
