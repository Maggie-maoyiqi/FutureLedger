"""Exact conversion at the text boundary, independent of Decimal precision."""

from decimal import Decimal

from .errors import AgentError


def yuan_to_cents(value: str, *, allow_zero: bool = False) -> int:
    numerator, denominator = Decimal(value).as_integer_ratio()
    scaled = numerator * 100
    cents, remainder = divmod(scaled, denominator)
    if remainder or cents < (0 if allow_zero else 1) or cents > 2**63 - 1:
        raise AgentError('INVALID_INTENT', 422, next_action='金额必须能精确表示为范围内的整数分。')
    return cents
