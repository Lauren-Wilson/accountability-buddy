from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _add_months(base_date: date, months: int, due_day: int | None = None) -> date:
    """Advance a date by whole months and clamp the target day safely."""
    zero_based_month = base_date.month - 1 + max(0, months)
    year = base_date.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    day = due_day or base_date.day
    last_day = monthrange(year, month)[1]
    return date(year, month, min(max(1, int(day)), last_day))


def estimate_payoff_schedule(
    balance: object,
    apr: object,
    payment: object,
    start_date: date,
    due_day: int | None = None,
    max_months: int = 600,
) -> dict[str, Any]:
    """Estimate a liability payoff schedule using monthly interest accrual.

    The loop intentionally models interest once per month and then applies the
    payment, which keeps the MVP math readable and stable for manual planning.
    """
    remaining_balance = max(0.0, _safe_float(balance))
    annual_rate = max(0.0, _safe_float(apr)) / 100.0
    scheduled_payment = max(0.0, _safe_float(payment))
    monthly_rate = annual_rate / 12.0

    if remaining_balance <= 0:
        return {
            "months_remaining": 0,
            "payoff_date": start_date,
            "total_interest": 0.0,
            "status": "paid_off",
        }

    if scheduled_payment <= 0:
        return {
            "months_remaining": None,
            "payoff_date": None,
            "total_interest": None,
            "status": "no_payment",
        }

    months = 0
    total_interest = 0.0
    running_balance = remaining_balance

    while running_balance > 0 and months < max_months:
        interest = running_balance * monthly_rate
        total_interest += interest
        running_balance += interest

        if scheduled_payment <= interest and running_balance > scheduled_payment:
            return {
                "months_remaining": None,
                "payoff_date": None,
                "total_interest": None,
                "status": "payment_too_low",
            }

        running_balance = max(0.0, running_balance - scheduled_payment)
        months += 1

    payoff_date = _add_months(start_date, months, due_day)

    status = "paid_off" if running_balance <= 0 else "max_months_reached"
    return {
        "months_remaining": months if status == "paid_off" else None,
        "payoff_date": payoff_date if status == "paid_off" else None,
        "total_interest": round(total_interest, 2) if status == "paid_off" else None,
        "status": status,
    }


def compare_payment_scenarios(
    balance: object,
    apr: object,
    current_payment: object,
    test_payment: object,
    start_date: date,
    due_day: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Compare the current liability payment with a higher trial payment."""
    current = estimate_payoff_schedule(balance, apr, current_payment, start_date, due_day)
    test = estimate_payoff_schedule(balance, apr, test_payment, start_date, due_day)
    return {"current": current, "test": test}
