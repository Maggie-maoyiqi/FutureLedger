"""Deterministic calendar-month comparisons without causal speculation."""

from .planner import month_bounds, shift_month
from .semantic import METRICS


def compare_period(plan, ledger, user):
    """Compare a scalar full-month amount with its preceding calendar month.

    Returned rows are category deltas (current minus preceding), under exactly
    the original authenticated user and filters. No raw transactions are returned.
    """
    if (
        plan.kind != "aggregate"
        or plan.group_by is not None
        or plan.series_by is not None
        or plan.metric not in ("gross_expense", "net_expense", "income", "refund")
    ):
        return None
    start, end = month_bounds(plan.start.year, plan.start.month)
    if plan.start != start or plan.end_exclusive != end:
        return None
    year, month = shift_month(start.year, start.month, -1)
    previous_start, previous_end = month_bounds(year, month)
    grouped = plan.model_copy(
        update={
            "group_by": "category",
            "series_by": None,
            "sort": "metric_desc",
            "limit": 100,
        }
    )
    current_rows = ledger.execute(grouped, user)["rows"]
    previous_rows = ledger.execute(
        grouped.model_copy(
            update={"start": previous_start, "end_exclusive": previous_end}
        ),
        user,
    )["rows"]
    current = {r["group_key"]: int(r["value_cents"] or 0) for r in current_rows}
    previous = {r["group_key"]: int(r["value_cents"] or 0) for r in previous_rows}
    rows = [
        {
            "group_key": category,
            "value_cents": current.get(category, 0) - previous.get(category, 0),
        }
        for category in current.keys() | previous.keys()
    ]
    rows.sort(key=lambda r: (-abs(r["value_cents"]), str(r["group_key"])))
    total, before = sum(current.values()), sum(previous.values())
    delta = total - before
    direction = "增加" if delta > 0 else "减少" if delta < 0 else "持平"
    change = f"{direction} ¥{abs(delta) / 100:.2f}" if delta else "持平"
    answer = (
        f"{start:%Y年%m月}{METRICS[plan.metric]}为 ¥{total / 100:.2f}，"
        f"前一个完整月（{previous_start:%Y年%m月}）为 ¥{before / 100:.2f}，{change}。"
    )
    contributors = [
        f"{r['group_key']}{'增加' if r['value_cents'] > 0 else '减少'} ¥{abs(r['value_cents']) / 100:.2f}"
        for r in rows[:3]
        if r["value_cents"]
    ]
    if contributors:
        answer += "按类别金额变化较大的是：" + "；".join(contributors) + "。"
    answer += "这是相同筛选口径下的金额差异，不能仅凭流水确定生活事件或涨价等原因。"
    return {"rows": rows, "answer": answer}
