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

from .planner import classify_route, make_intent, parse_transfer
from .provider import ChatProvider, ProviderError
from .cashflow import simulate_cashflow
from .query import Ledger, QueryError
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
    if metric in {"transaction_count", "transfer_count"}:
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


def simulate_plan_payload(plan_id: str, request: PlanSimulationRequest, *, user_id: str) -> dict[str, object]:
    """Build a deterministic simulation response without changing ledger state."""
    result = simulate_cashflow(
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
            return simulate_plan_payload(plan_id, request, user_id=user)
        except ValueError as exc:
            raise HTTPException(422, {"code": "INVALID_SIMULATION", "message": str(exc)}) from exc

    @app.post("/api/ask")
    async def ask(request: AskRequest):
        user = sessions.get(request.session_id)
        if user is None:
            raise HTTPException(403, {"code": "FORBIDDEN", "message": "无效或已失效的演示会话"})
        try:
            decision = await classify_route(request.question, model)
        except ProviderError as exc:
            raise HTTPException(502, {"code": "PROVIDER_ERROR", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(502, {"code": "INVALID_ROUTE", "message": "模型路由结果无效，请重试。"}) from exc
        route, route_message = decision["route"], decision.get("message")
        if route == "planning":
            route_message = ("已识别为存钱目标／省钱规划请求。当前聊天入口只负责识别意图；"
                             "请由后端校验计划参数后调用 /api/plans/{plan_id}/simulate 获取逐日现金流结果。"
                             "不会把全量流水交给模型，也不会据此执行转账。")
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

        previous = pending.get(request.session_id)
        is_followup = bool(previous and len(request.question) <= 80 and not re.search(r"20\d{2}|上个?月|本月|最近\s*\d+\s*天", request.question))
        effective_question = previous + "；补充条件：" + request.question if is_followup else request.question
        try:
            accounts = ledger.accounts(user)
            merchants = ledger.merchants(user, accounts)
            user_context = ledger.llm_context(user, accounts, limit=30)
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
        except ValidationError:
            return {"status": "clarification", "route": "query", "message": "查询条件、时间或指标不完整，请明确后重试。"}
        except QueryError as exc:
            status = "clarification" if exc.code in {"UNKNOWN_ENUM", "UNKNOWN_ENTITY"} else "blocked"
            return {"status": status, "route": "query", "code": exc.code, "message": str(exc)}

        rows = executed["rows"]
        pending.pop(request.session_id, None)
        if plan.kind == "detail":
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
        queries[query_id] = {"session_id": request.session_id, "rows": rows}
        return {
            "status": "answer", "route": "query", "answer": answer, "rows": rows,
            "chart": chart_for(plan, effective_question),
            "value_format": "count" if plan.metric in {"transaction_count", "transfer_count"} else "money",
            "query_id": query_id,
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
        writer.writerow(fields)
        for row in rows:
            writer.writerow([csv_safe(row.get(field)) for field in fields])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="futureledger-{query_id}.csv"'})

    return app


app = create_app()
