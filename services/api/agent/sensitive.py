"""Reject recognisable credentials; this is not a general-purpose DLP system."""

import re

from .errors import AgentError


SECRET = re.compile(r'(?i)(?:\bsk-[a-z0-9_-]{16,}|\b(?:api[ _-]?key|bearer)\s*[:：=]?\s*[a-z0-9_-]{12,}'
                    r'|(?:密码|验证码|\bpassword|\bpasswd|\botp)\s*[:：=]\s*[^\s,，}\"]+|(?:验证码|\botp)\s*\d{4,8}(?!\d))')


def contains_secret(text: str) -> bool:
    return bool(SECRET.search(text))


def reject_credentials(text: str):
    if contains_secret(text):
        raise AgentError('SENSITIVE_INPUT',422,next_action='请移除凭证，使用独立 B 验证表单。')
