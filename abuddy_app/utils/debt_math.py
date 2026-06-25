from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

import pandas as pd


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


def sort_group_liabilities(liabilities: pd.DataFrame, strategy: str) -> pd.DataFrame:
    strategy_name = str(strategy or "avalanche").strip().lower()
    frame = liabilities.copy()

    if strategy_name == "snowball":
        return frame.sort_values(["balance", "apr", "priority", "name"], ascending=[True, False, True, True])
    if strategy_name == "priority":
        return frame.sort_values(["priority", "apr", "balance", "name"], ascending=[True, False, False, True])
    if strategy_name == "proportional":
        return frame.sort_values(["balance", "apr", "name"], ascending=[False, False, True])
    return frame.sort_values(["apr", "balance", "priority", "name"], ascending=[False, False, True, True])


def _build_month_state(liabilities: pd.DataFrame) -> pd.DataFrame:
    state = liabilities.copy()
    state["starting_balance"] = state["balance"].apply(lambda value: max(0.0, _safe_float(value)))
    state["apr"] = state["apr"].apply(lambda value: max(0.0, _safe_float(value)))
    state["interest_applied"] = state["starting_balance"] * (state["apr"] / 100.0 / 12.0)
    state["balance_with_interest"] = state["starting_balance"] + state["interest_applied"]
    state["amount_applied"] = 0.0
    return state


def apply_group_payment(
    liabilities: pd.DataFrame,
    payment_amount: float,
    strategy: str,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Apply one monthly payment across active liabilities and return updated rows + payment rows.

    Returns: (updated_liabilities, log_rows, unapplied_remainder)
    """
    active = liabilities[liabilities["active"] & (liabilities["balance"] > 0)].copy()
    if active.empty or payment_amount <= 0:
        empty_log = pd.DataFrame(
            columns=[
                "liability_id",
                "amount_applied",
                "interest_applied",
                "principal_applied",
                "starting_balance",
                "ending_balance",
            ]
        )
        return liabilities.copy(), empty_log, max(0.0, payment_amount)

    working = _build_month_state(active)
    remaining_payment = max(0.0, _safe_float(payment_amount))
    strategy_name = str(strategy or "avalanche").strip().lower()

    if strategy_name == "proportional":
        while remaining_payment > 0.000001:
            due_total = float((working["balance_with_interest"] - working["amount_applied"]).clip(lower=0.0).sum())
            if due_total <= 0:
                break

            allocations = []
            for idx, row in working.iterrows():
                due_amount = max(0.0, float(row["balance_with_interest"] - row["amount_applied"]))
                if due_amount <= 0:
                    allocations.append((idx, 0.0))
                    continue
                share = due_amount / due_total
                proposed = remaining_payment * share
                apply_now = min(due_amount, proposed)
                allocations.append((idx, apply_now))

            allocated_this_round = 0.0
            for idx, apply_now in allocations:
                if apply_now <= 0:
                    continue
                working.loc[idx, "amount_applied"] += apply_now
                allocated_this_round += apply_now

            remaining_payment = max(0.0, remaining_payment - allocated_this_round)
            if allocated_this_round <= 0.000001:
                break

        # Push any rounding pennies to largest remaining due liability.
        if remaining_payment > 0.000001:
            due = (working["balance_with_interest"] - working["amount_applied"]).clip(lower=0.0)
            if float(due.sum()) > 0:
                idx = due.idxmax()
                apply_now = min(float(due.loc[idx]), remaining_payment)
                working.loc[idx, "amount_applied"] += apply_now
                remaining_payment = max(0.0, remaining_payment - apply_now)
    else:
        ordered = sort_group_liabilities(working, strategy_name)
        for _, row in ordered.iterrows():
            if remaining_payment <= 0:
                break
            idx = row.name
            due_amount = max(0.0, float(working.loc[idx, "balance_with_interest"] - working.loc[idx, "amount_applied"]))
            if due_amount <= 0:
                continue
            apply_now = min(due_amount, remaining_payment)
            working.loc[idx, "amount_applied"] += apply_now
            remaining_payment -= apply_now

    working["principal_applied"] = (working["amount_applied"] - working["interest_applied"]).clip(lower=0.0)
    working["ending_balance"] = (working["balance_with_interest"] - working["amount_applied"]).clip(lower=0.0)
    working["ending_balance"] = working["ending_balance"].round(2)
    working["interest_applied"] = working["interest_applied"].round(2)
    working["amount_applied"] = working["amount_applied"].round(2)
    working["principal_applied"] = working["principal_applied"].round(2)
    working["active"] = working["ending_balance"] > 0

    updated = liabilities.copy()
    updates = working[["liability_id", "ending_balance", "active"]].copy()
    updates = updates.rename(columns={"ending_balance": "balance"})
    updated = updated.drop(columns=["balance", "active"]).merge(updates, on="liability_id", how="left")
    updated["balance"] = updated["balance"].fillna(liabilities["balance"])
    updated["active"] = updated["active"].fillna(liabilities["active"]) 

    log_rows = working[
        [
            "liability_id",
            "amount_applied",
            "interest_applied",
            "principal_applied",
            "starting_balance",
            "ending_balance",
        ]
    ].copy()
    log_rows = log_rows[log_rows["amount_applied"] > 0].reset_index(drop=True)
    return updated, log_rows, round(max(0.0, remaining_payment), 2)


def estimate_group_payoff(
    liabilities: pd.DataFrame,
    payment_amount: float,
    strategy: str,
    start_date: date,
    max_months: int = 600,
) -> dict[str, Any]:
    active = liabilities[liabilities["active"] & (liabilities["balance"] > 0)].copy()
    if active.empty:
        return {"months_remaining": 0, "projected_payoff_date": start_date, "status": "paid_off"}

    monthly_payment = max(0.0, _safe_float(payment_amount))
    if monthly_payment <= 0:
        return {"months_remaining": None, "projected_payoff_date": None, "status": "no_payment"}

    simulation = active.copy()
    months = 0

    while months < max_months:
        if float(simulation.loc[simulation["active"], "balance"].sum()) <= 0.000001:
            payoff_date = _add_months(start_date, months)
            return {
                "months_remaining": months,
                "projected_payoff_date": payoff_date,
                "status": "paid_off",
            }

        before_total = float(simulation.loc[simulation["active"], "balance"].sum())
        simulation, _, _ = apply_group_payment(simulation, monthly_payment, strategy)
        after_total = float(simulation.loc[simulation["active"], "balance"].sum())
        if after_total >= before_total - 0.000001:
            return {"months_remaining": None, "projected_payoff_date": None, "status": "payment_too_low"}
        months += 1

    return {"months_remaining": None, "projected_payoff_date": None, "status": "max_months_reached"}
