"""Deterministic integer-cent cash-flow simulation for the demo ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CashflowResult:
    """A reproducible daily cash-flow result suitable for API serialization."""

    points_cents: tuple[int, ...]
    min_balance_cents: int
    min_day: int
    feasible: bool
    conflicts: tuple[dict[str, int | str], ...]
    assumptions: dict[str, int]
    events: tuple[dict[str, int | str | bool], ...]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["points_cents"] = list(self.points_cents)
        result["conflicts"] = [dict(item) for item in self.conflicts]
        result["events"] = [dict(item) for item in self.events]
        return result


def _validate_int(value: object, name: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def simulate_cashflow(
    *,
    salary_day: int,
    purchase_day: int,
    daily_budget_cents: int = 8000,
    opening_balance_cents: int = 900000,
    floor_cents: int = 300000,
    goal_cents: int = 600000,
    days: int = 30,
) -> CashflowResult:
    """Simulate the fixed FutureLedger demo scenario day by day.

    The demo scenario intentionally uses fixed synthetic events so benchmark
    cases are reproducible: rent on day 5, cloud storage on day 8, video on
    day 10, salary on the selected salary day, and the computer purchase on
    the selected purchase day. The daily budget is applied at the end of each
    day, matching the published benchmark oracle.
    """
    salary_day = _validate_int(salary_day, "salary_day", minimum=1)
    purchase_day = _validate_int(purchase_day, "purchase_day", minimum=1)
    daily_budget_cents = _validate_int(daily_budget_cents, "daily_budget_cents", minimum=0)
    opening_balance_cents = _validate_int(opening_balance_cents, "opening_balance_cents", minimum=0)
    floor_cents = _validate_int(floor_cents, "floor_cents", minimum=0)
    goal_cents = _validate_int(goal_cents, "goal_cents", minimum=0)
    days = _validate_int(days, "days", minimum=1)
    if salary_day > days or purchase_day > days:
        raise ValueError("event day must be within the simulation")

    events: list[dict[str, int | str | bool]] = [
        {"day": 5, "signed_cents": -350000, "source": "rent", "protected": True},
        {"day": 8, "signed_cents": -3000, "source": "cloud_storage", "protected": True},
        {"day": 10, "signed_cents": -6800, "source": "video_subscription", "protected": True},
        {"day": salary_day, "signed_cents": 800000, "source": "salary", "assumption": True},
        {"day": purchase_day, "signed_cents": -600000, "source": "computer_goal", "assumption": True},
    ]

    points: list[int] = []
    balance = opening_balance_cents
    for day in range(1, days + 1):
        balance += sum(int(event["signed_cents"]) for event in events if event["day"] == day)
        balance -= daily_budget_cents
        points.append(balance)

    min_balance = min(points)
    min_day = points.index(min_balance) + 1
    feasible = min_balance >= floor_cents
    conflicts: tuple[dict[str, int | str], ...] = ()
    if not feasible:
        conflicts = (
            {
                "code": "PROTECTED_BALANCE_BREACH",
                "day": min_day,
                "balance_cents": min_balance,
                "floor_cents": floor_cents,
            },
        )

    return CashflowResult(
        points_cents=tuple(points),
        min_balance_cents=min_balance,
        min_day=min_day,
        feasible=feasible,
        conflicts=conflicts,
        assumptions={
            "opening_balance_cents": opening_balance_cents,
            "floor_cents": floor_cents,
            "goal_cents": goal_cents,
            "daily_budget_cents": daily_budget_cents,
            "days": days,
        },
        events=tuple(events),
    )
