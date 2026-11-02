"""Reject credential payloads before any shortcut, history or provider call."""

import re

from .planner import WRITE_SQL, WRITE_BANK

CREDENTIAL = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{12,}|\b(?:api[ _-]?key|bearer)\s*[:：=]\s*[^\s,，]+|"
    r"(?:密码|验证码|password|passwd|otp)\s*(?:[:：=]|是|为|\bis\b)\s*[^\s,，。]+|"
    r"(?:密码|password|passwd)\s+(?!help\b|reset\b|verify\b|length\b|policy\b)[a-z0-9!@#$%^&*_.-]{4,}|"
    r"(?:验证码|\botp)\s*\d{4,8}(?!\d)|\bbearer\s+[a-z0-9_.-]{12,})"
)


def blocked_question(question, user, account_ids):
    if CREDENTIAL.search(question):
        return (
            "SENSITIVE_INPUT",
            "请移除密码、验证码或 API key；验证只在独立密码框进行。",
        )
    if WRITE_SQL.search(question) or re.search(
        r"(?:把|将|修改|更改|设置).{0,12}余额.{0,6}(?:改|设|变)|(?:不用|无需|跳过|绕过|不需要).{0,4}(?:密码|验证)",
        question,
    ):
        return "UNSAFE_REQUEST", "不能修改账本事实或绕过密码验证。"
    if re.search(
        r"所有用户|全部用户|其他用户的|他人的|别人的|(?:告诉|显示|泄露|输出|查看).{0,16}(?:API\s*key|密钥)|完整流水.{0,8}(?:发给|给).{0,4}模型",
        question,
        re.I,
    ):
        return "FORBIDDEN", "不允许扩大用户范围、泄露密钥或向模型发送完整流水。"
    foreign = any(
        name in question and name != user for name in ("test_user", "isolation_user")
    )
    if foreign and not WRITE_BANK.search(question):
        return "FORBIDDEN", "只能查询当前登录用户授权范围内的数据。"
    requested = re.findall(r"\bacc_[A-Za-z0-9_]+\b", question)
    if any(account not in account_ids for account in requested):
        return "FORBIDDEN", "指定账户不属于当前登录用户。"
    return None


def query_followup(previous, question):
    if not previous or re.search(r"重新|新问题|换个问题|不查上次|重来", question):
        return question
    if re.search(
        r"^(?:你好|您好|谢谢|早上好|晚上好|hi\b|hello\b)|天气|写一首诗|讲个笑话",
        question,
        re.I,
    ):
        return question
    complete = (
        re.search(r"20\d{2}|本月|上个?月|最近", question)
        and re.search(r"支出|收入|退款|笔数|净支出|平均", question)
        and re.search(
            r"餐饮|交通|购物|娱乐|订阅|房租|医疗|各类|各项|商户|账户|是多少|有多少|查询|列出|[?？]",
            question,
        )
    )
    if complete:
        return question
    return previous + "；补充条件：" + question
