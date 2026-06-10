from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from utils import gsheets
from utils.pay_schedule import (
    DEFAULT_KNOWN_PAYDAY,
    DEFAULT_PAY_INTERVAL_DAYS,
    clamp_day,
    get_current_pay_period,
    get_paydays_between,
    get_next_payday,
    get_remaining_paydays_in_month,
)

DATA_FILES = {
    "transactions": "transactions.csv",
    "recurring_bills": "recurring_bills.csv",
    "liabilities": "liabilities.csv",
    "settings": "settings.csv",
}

EXPECTED_COLUMNS = {
    "transactions": ["date", "amount", "category", "note", "transaction_type"],
    "recurring_bills": ["bill_name", "amount", "due_day", "category", "active"],
    "liabilities": ["name", "balance", "apr", "current_payment", "due_day"],
    "settings": ["setting", "value"],
}

DEFAULT_SETTINGS = {
    "known_payday": "2026-06-12",
    "pay_interval_days": "14",
    "paycheck_amount": "1450",
    "starting_available_cash": "1850",
    "starting_cash_as_of": "",
    "leftover_from_prior_month": "180",
    "app_name": "A-Buddy",
}

SAMPLE_ROWS = {
    "transactions": [
        {
            "date": "2026-05-29",
            "amount": 1450,
            "category": "Paycheck",
            "note": "Biweekly paycheck",
            "transaction_type": "income",
        },
        {
            "date": "2026-06-03",
            "amount": 45,
            "category": "Groceries",
            "note": "Quick restock",
            "transaction_type": "spend",
        },
        {
            "date": "2026-06-05",
            "amount": 75,
            "category": "Visa Card",
            "note": "Manual debt payment",
            "transaction_type": "debt_payment",
        },
        {
            "date": "2026-06-08",
            "amount": 18,
            "category": "Coffee",
            "note": "Coffee meetup",
            "transaction_type": "spend",
        },
        {
            "date": "2026-06-09",
            "amount": 25,
            "category": "Transport",
            "note": "Gas top-off",
            "transaction_type": "spend",
        },
    ],
    "recurring_bills": [
        {"bill_name": "Rent", "amount": 950, "due_day": 1, "category": "Housing", "active": True},
        {"bill_name": "Phone", "amount": 80, "due_day": 15, "category": "Utilities", "active": True},
        {"bill_name": "Internet", "amount": 60, "due_day": 18, "category": "Utilities", "active": True},
        {"bill_name": "Car Insurance", "amount": 110, "due_day": 22, "category": "Transportation", "active": True},
        {"bill_name": "Spotify", "amount": 12, "due_day": 25, "category": "Fun", "active": True},
    ],
    "liabilities": [
        {"name": "Visa Card", "balance": 1200, "apr": 24.99, "current_payment": 75, "due_day": 20},
        {"name": "Student Loan", "balance": 5400, "apr": 5.0, "current_payment": 120, "due_day": 28},
    ],
    "settings": [{"setting": key, "value": value} for key, value in DEFAULT_SETTINGS.items()],
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return default


def _parse_date(value: object, fallback: date | None = None) -> date:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback or date.today()


def _month_start(reference_date: date) -> date:
    return reference_date.replace(day=1)


def _month_end(reference_date: date) -> date:
    return reference_date.replace(day=monthrange(reference_date.year, reference_date.month)[1])


def ensure_data_files(data_dir: Path) -> None:
    """Bootstrap the data layer on first run.

    When Google Sheets credentials are configured the function creates any
    missing worksheet tabs and seeds them with sample data.  Otherwise it
    falls back to the original local-CSV behaviour.
    """
    if gsheets.is_configured():
        try:
            gsheets.ensure_sheet_tabs(EXPECTED_COLUMNS, SAMPLE_ROWS)
        except Exception as exc:
            st.warning(f"Google Sheets bootstrap is temporarily unavailable: {exc}")
        return

    # --- local CSV fallback ---
    data_dir.mkdir(parents=True, exist_ok=True)
    for key, filename in DATA_FILES.items():
        path = data_dir / filename
        expected_columns = EXPECTED_COLUMNS[key]
        if not path.exists():
            pd.DataFrame(SAMPLE_ROWS[key], columns=expected_columns).to_csv(path, index=False)
            continue

        try:
            existing = pd.read_csv(path)
        except Exception:
            existing = pd.DataFrame(columns=expected_columns)
        for column in expected_columns:
            if column not in existing.columns:
                existing[column] = ""
        existing = existing[expected_columns]
        existing.to_csv(path, index=False)

        if existing.empty and SAMPLE_ROWS[key]:
            pd.DataFrame(SAMPLE_ROWS[key], columns=expected_columns).to_csv(path, index=False)


def _read_csv(data_dir: Path, key: str) -> pd.DataFrame:
    expected_columns = EXPECTED_COLUMNS[key]
    if gsheets.is_configured():
        return gsheets.read_sheet(key, expected_columns)

    # --- local CSV fallback ---
    path = data_dir / DATA_FILES[key]
    if not path.exists():
        return pd.DataFrame(columns=expected_columns)
    try:
        frame = pd.read_csv(path)
    except Exception:
        frame = pd.DataFrame(columns=expected_columns)
    for column in expected_columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame[expected_columns].copy()


def load_settings(data_dir: Path) -> dict[str, str]:
    settings_frame = _read_csv(data_dir, "settings")
    settings = DEFAULT_SETTINGS.copy()
    seen_keys: set[str] = set()
    settings_frame["setting"] = settings_frame["setting"].astype(str).str.strip()
    settings_frame["value"] = settings_frame["value"].astype(str).str.strip()
    for _, row in settings_frame.iterrows():
        key = str(row.get("setting", "")).strip()
        if key and key not in seen_keys:
            settings[key] = str(row.get("value", "")).strip()
            seen_keys.add(key)
    return settings


def load_transactions(data_dir: Path) -> pd.DataFrame:
    transactions = _read_csv(data_dir, "transactions")
    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce").fillna(0.0)
    transactions["category"] = transactions["category"].fillna("Uncategorized").astype(str).str.strip()
    transactions["note"] = transactions["note"].fillna("").astype(str).str.strip()
    transactions["transaction_type"] = (
        transactions["transaction_type"].fillna("spend").astype(str).str.strip().str.lower()
    )
    transactions.loc[transactions["category"] == "", "category"] = "Uncategorized"
    transactions = transactions.dropna(subset=["date"]).sort_values("date")
    return transactions


def load_recurring_bills(data_dir: Path) -> pd.DataFrame:
    bills = _read_csv(data_dir, "recurring_bills")

    # Defensive normalization for hand-edited sheet headers.
    normalized_columns = {str(col).strip().lower(): col for col in bills.columns}
    if "due_day" not in bills.columns:
        for alias in ["due day", "due_date", "due-date", "dueday"]:
            if alias in normalized_columns:
                bills["due_day"] = bills[normalized_columns[alias]]
                break
    if "active" not in bills.columns:
        bills["active"] = True
    if "bill_name" not in bills.columns:
        bills["bill_name"] = ""
    if "category" not in bills.columns:
        bills["category"] = ""

    bills["amount"] = pd.to_numeric(bills["amount"], errors="coerce").fillna(0.0)
    bills["due_day"] = pd.to_numeric(bills["due_day"], errors="coerce").fillna(1).astype(int)
    bills["active"] = bills["active"].apply(lambda value: _safe_bool(value, True))
    bills["bill_name"] = bills["bill_name"].fillna("Unnamed Bill").astype(str).str.strip()
    bills["category"] = bills["category"].fillna("Bills").astype(str).str.strip()
    bills.loc[bills["bill_name"] == "", "bill_name"] = "Unnamed Bill"
    bills.loc[bills["category"] == "", "category"] = "Bills"
    return bills


def load_liabilities(data_dir: Path) -> pd.DataFrame:
    liabilities = _read_csv(data_dir, "liabilities")
    for column in ["balance", "apr", "current_payment"]:
        liabilities[column] = pd.to_numeric(liabilities[column], errors="coerce").fillna(0.0)
    liabilities["due_day"] = pd.to_numeric(liabilities["due_day"], errors="coerce").fillna(1).astype(int)
    liabilities["name"] = liabilities["name"].fillna("Unnamed Liability").astype(str).str.strip()
    liabilities.loc[liabilities["name"] == "", "name"] = "Unnamed Liability"
    return liabilities


def derive_liability_balances(
    liabilities: pd.DataFrame,
    transactions: pd.DataFrame,
    reference_date: date,
) -> pd.DataFrame:
    """Return liabilities with current balances derived from debt payment transactions.

    The liabilities sheet continues to hold the original balance seed, but the
    current balance shown in the app is computed from transaction history so the
    ledger is the source of truth.
    """
    derived = liabilities.copy()
    if derived.empty:
        derived["original_balance"] = pd.Series(dtype="float64")
        derived["paid_to_date"] = pd.Series(dtype="float64")
        derived["current_balance"] = pd.Series(dtype="float64")
        return derived

    tx = transactions.copy()
    if tx.empty:
        derived["original_balance"] = pd.to_numeric(derived["balance"], errors="coerce").fillna(0.0)
        derived["paid_to_date"] = 0.0
        derived["current_balance"] = derived["original_balance"].clip(lower=0.0)
        return derived

    tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
    tx = tx.dropna(subset=["date"])
    tx = tx[tx["date"].dt.date <= reference_date].copy()
    tx["transaction_type"] = tx["transaction_type"].fillna("").astype(str).str.strip().str.lower()
    tx["category"] = tx["category"].fillna("").astype(str).str.strip().str.lower()

    debt_payments = tx[tx["transaction_type"] == "debt_payment"].copy()
    debt_payments["amount"] = pd.to_numeric(debt_payments["amount"], errors="coerce").fillna(0.0).abs()

    derived["original_balance"] = pd.to_numeric(derived["balance"], errors="coerce").fillna(0.0)
    paid_totals: list[float] = []
    current_balances: list[float] = []

    for _, liability in derived.iterrows():
        liability_name = str(liability.get("name", "")).strip().lower()
        paid_to_date = round(
            debt_payments.loc[debt_payments["category"] == liability_name, "amount"].sum(),
            2,
        )
        current_balance = round(max(0.0, _safe_float(liability.get("original_balance", 0.0)) - paid_to_date), 2)
        paid_totals.append(paid_to_date)
        current_balances.append(current_balance)

    derived["paid_to_date"] = paid_totals
    derived["current_balance"] = current_balances
    return derived


def append_transaction(data_dir: Path, row: dict[str, Any]) -> None:
    if gsheets.is_configured():
        gsheets.append_row("transactions", row, EXPECTED_COLUMNS["transactions"])
        return

    # --- local CSV fallback ---
    path = data_dir / DATA_FILES["transactions"]
    transactions = _read_csv(data_dir, "transactions")
    next_row = pd.DataFrame([row], columns=EXPECTED_COLUMNS["transactions"])
    combined = pd.concat([transactions, next_row], ignore_index=True)
    combined.to_csv(path, index=False)


def auto_add_due_paychecks(
    data_dir: Path,
    transactions: pd.DataFrame,
    settings: dict[str, str],
    reference_date: date,
) -> int:
    """Append missing scheduled paycheck income rows up through reference_date.

    Returns the count of auto-added paycheck transactions.
    """
    paycheck_amount = max(0.0, _safe_float(settings.get("paycheck_amount"), 0.0))
    if paycheck_amount <= 0:
        return 0

    anchor_payday = _parse_date(settings.get("known_payday"), DEFAULT_KNOWN_PAYDAY)
    pay_interval_days = max(1, _safe_int(settings.get("pay_interval_days"), DEFAULT_PAY_INTERVAL_DAYS))
    starting_cash_as_of_setting = str(settings.get("starting_cash_as_of", "")).strip()
    baseline_start = (
        _parse_date(starting_cash_as_of_setting, anchor_payday)
        if starting_cash_as_of_setting
        else reference_date
    )

    tx = transactions.copy()
    tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
    tx = tx.dropna(subset=["date"])
    tx["transaction_type"] = tx["transaction_type"].fillna("").astype(str).str.strip().str.lower()
    tx["category"] = tx["category"].fillna("").astype(str).str.strip().str.lower()
    tx["amount"] = pd.to_numeric(tx["amount"], errors="coerce").fillna(0.0)

    paycheck_rows = tx[
        (tx["transaction_type"] == "income")
        & (tx["category"] == "paycheck")
        & ((tx["amount"].abs() - paycheck_amount).abs() <= 0.01)
    ]

    if not paycheck_rows.empty:
        latest_logged_paycheck = paycheck_rows["date"].dt.date.max()
        start_date = max(baseline_start, latest_logged_paycheck + timedelta(days=1))
    else:
        start_date = baseline_start

    if start_date > reference_date:
        return 0

    due_paydays = get_paydays_between(
        start_date=start_date,
        end_date=reference_date,
        anchor_payday=anchor_payday,
        interval_days=pay_interval_days,
    )

    # Never auto-add future paychecks; only due dates at or before reference_date.
    due_paydays = [payday for payday in due_paydays if payday <= reference_date]

    added_count = 0
    for payday in due_paydays:
        already_exists = not tx[
            (tx["transaction_type"] == "income")
            & (tx["category"] == "paycheck")
            & (tx["date"].dt.date == payday)
            & ((tx["amount"].abs() - paycheck_amount).abs() <= 0.01)
        ].empty
        if already_exists:
            continue

        row = {
            "date": payday.isoformat(),
            "amount": paycheck_amount,
            "category": "Paycheck",
            "note": "Auto-added biweekly paycheck",
            "transaction_type": "income",
        }

        try:
            append_transaction(data_dir, row)
            added_count += 1

            tx = pd.concat([tx, pd.DataFrame([{
                "date": pd.to_datetime(payday),
                "amount": paycheck_amount,
                "category": "paycheck",
                "note": "Auto-added biweekly paycheck",
                "transaction_type": "income",
            }])], ignore_index=True)
        except Exception as exc:
            st.warning(f"Could not auto-add paycheck for {payday.isoformat()}: {exc}")

    return added_count


def get_category_options(transactions: pd.DataFrame, bills: pd.DataFrame, liabilities: pd.DataFrame) -> list[str]:
    raw_categories = set(transactions["category"].dropna().astype(str).tolist())
    raw_categories.update(bills["category"].dropna().astype(str).tolist())
    raw_categories.update(liabilities["name"].dropna().astype(str).tolist())
    raw_categories.update({"Groceries", "Dining", "Transport", "Fun", "Paycheck", "Adjustment"})
    return sorted(category for category in raw_categories if category)


def _signed_amount(row: pd.Series) -> float:
    amount = _safe_float(row.get("amount", 0.0))
    transaction_type = str(row.get("transaction_type", "spend")).strip().lower()
    if transaction_type == "income":
        return abs(amount)
    if transaction_type in {"spend", "debt_payment"}:
        return -abs(amount)
    if transaction_type == "adjustment":
        return amount
    return -abs(amount)


def get_leftover_from_prior_month(settings: dict[str, str], transactions: pd.DataFrame, reference_date: date) -> float:
    """Use the prior-month carryover setting as the MVP default.

    If the local ledger already has historical activity, this can later evolve to
    a fully derived carryover value. The setting keeps first-run behavior stable.
    """
    configured_leftover = _safe_float(settings.get("leftover_from_prior_month"), 0.0)
    return max(0.0, configured_leftover)


def get_recurring_bills_for_month(bills: pd.DataFrame, reference_date: date) -> pd.DataFrame:
    if "due_day" not in bills.columns:
        bills = bills.copy()
        bills["due_day"] = 1
    if "active" not in bills.columns:
        bills = bills.copy()
        bills["active"] = True

    active_bills = bills[bills["active"]].copy()
    active_bills["due_date"] = active_bills["due_day"].apply(
        lambda due_day: clamp_day(reference_date.year, reference_date.month, due_day)
    )
    return active_bills.sort_values(["due_date", "bill_name"])


def build_budget_snapshot(
    transactions: pd.DataFrame,
    recurring_bills: pd.DataFrame,
    settings: dict[str, str],
    reference_date: date,
) -> dict[str, Any]:
    month_start = _month_start(reference_date)
    month_end = _month_end(reference_date)
    anchor_payday = _parse_date(settings.get("known_payday"), DEFAULT_KNOWN_PAYDAY)
    pay_interval_days = max(1, _safe_int(settings.get("pay_interval_days"), DEFAULT_PAY_INTERVAL_DAYS))
    paycheck_amount = max(0.0, _safe_float(settings.get("paycheck_amount"), 0.0))
    starting_cash = _safe_float(settings.get("starting_available_cash"), 0.0)
    leftover_from_prior_month = get_leftover_from_prior_month(settings, transactions, reference_date)
    starting_cash_as_of_setting = str(settings.get("starting_cash_as_of", "")).strip()

    transactions = transactions.copy()
    transactions["signed_amount"] = transactions.apply(_signed_amount, axis=1)
    if starting_cash_as_of_setting:
        starting_cash_as_of = min(
            _parse_date(starting_cash_as_of_setting, reference_date),
            reference_date,
        )
    else:
        starting_cash_as_of = min(anchor_payday, reference_date)

    through_today = transactions[
        (transactions["date"].dt.date >= starting_cash_as_of) & (transactions["date"].dt.date <= reference_date)
    ]
    current_available_balance = round(starting_cash + through_today["signed_amount"].sum(), 2)

    current_month = transactions[
        (transactions["date"].dt.date >= month_start) & (transactions["date"].dt.date <= month_end)
    ].copy()
    manual_spending_total = round(
        current_month.loc[current_month["transaction_type"] == "spend", "amount"].abs().sum(),
        2,
    )
    debt_payments_total = round(
        current_month.loc[current_month["transaction_type"] == "debt_payment", "amount"].abs().sum(),
        2,
    )

    bills_this_month = get_recurring_bills_for_month(recurring_bills, reference_date)
    recurring_bills_total = round(bills_this_month["amount"].sum(), 2)

    bill_payment_transactions = current_month[
        current_month["transaction_type"].isin(["spend", "debt_payment"])
    ].copy()
    bill_payment_transactions["remaining_payment"] = bill_payment_transactions["amount"].abs()

    paid_flags: list[bool] = []
    for _, bill in bills_this_month.iterrows():
        bill_name = str(bill["bill_name"]).strip().lower()
        bill_amount = _safe_float(bill["amount"], 0.0)
        if bill_amount <= 0:
            paid_flags.append(False)
            continue

        matching_index = bill_payment_transactions[
            bill_payment_transactions["category"].str.lower() == bill_name
        ].index

        paid_amount = 0.0
        for index in matching_index:
            remaining = _safe_float(bill_payment_transactions.at[index, "remaining_payment"], 0.0)
            if remaining <= 0:
                continue
            needed = bill_amount - paid_amount
            used = min(needed, remaining)
            paid_amount += used
            bill_payment_transactions.at[index, "remaining_payment"] = remaining - used
            if paid_amount >= bill_amount - 0.005:
                break

        paid_flags.append(paid_amount >= bill_amount - 0.005)

    bills_this_month["is_paid"] = paid_flags
    paid_bills = bills_this_month[bills_this_month["is_paid"]]
    remaining_bills = bills_this_month[~bills_this_month["is_paid"]]
    paid_recurring_bills_total = round(paid_bills["amount"].sum(), 2)
    remaining_recurring_bills_total = round(remaining_bills["amount"].sum(), 2)

    next_payday = get_next_payday(reference_date, anchor_payday, pay_interval_days)
    pay_period_start, pay_period_end = get_current_pay_period(reference_date, anchor_payday, pay_interval_days)
    remaining_paydays = get_remaining_paydays_in_month(reference_date, anchor_payday, pay_interval_days)
    future_income_this_month = round(len(remaining_paydays) * paycheck_amount, 2)

    # Cash-based forecast: what is available now plus paychecks still to arrive,
    # minus recurring bills that have not come due yet.
    projected_end_of_month_balance = round(
        current_available_balance + future_income_this_month - remaining_recurring_bills_total,
        2,
    )

    leftover_slice = round(min(max(current_available_balance, 0.0), leftover_from_prior_month), 2)
    remaining_balance_slice = round(max(current_available_balance - leftover_slice, 0.0), 2)
    available_monthly_balance = round(max(current_available_balance - remaining_recurring_bills_total, 0.0), 2)
    current_pay_period = transactions[
        (transactions["date"].dt.date >= pay_period_start) & (transactions["date"].dt.date <= reference_date)
    ].copy()
    recurring_bill_names = set(bills_this_month["bill_name"].astype(str).str.strip().str.lower())
    recurring_spend_categories = current_pay_period["category"].astype(str).str.strip().str.lower().isin(
        recurring_bill_names
    )
    current_pay_period_spending_total = round(
        current_pay_period.loc[
            current_pay_period["transaction_type"].isin(["spend", "debt_payment"]) & ~recurring_spend_categories,
            "amount",
        ]
        .abs()
        .sum(),
        2,
    )
    pay_period_remaining_recurring_bills = remaining_bills[
        (remaining_bills["due_date"] >= reference_date) & (remaining_bills["due_date"] <= pay_period_end)
    ]
    pay_period_remaining_recurring_total = round(pay_period_remaining_recurring_bills["amount"].sum(), 2)
    pay_period_available_funds = round(max(current_available_balance - pay_period_remaining_recurring_total, 0.0), 2)

    return {
        "reference_date": reference_date,
        "month_start": month_start,
        "month_end": month_end,
        "starting_cash_as_of": starting_cash_as_of,
        "current_available_balance": current_available_balance,
        "recurring_bills_total": recurring_bills_total,
        "paid_recurring_bills_total": paid_recurring_bills_total,
        "manual_spending_total": manual_spending_total,
        "debt_payments_total": debt_payments_total,
        "leftover_from_prior_month": leftover_from_prior_month,
        "leftover_slice": leftover_slice,
        "remaining_balance_slice": remaining_balance_slice,
        "remaining_recurring_bills_total": remaining_recurring_bills_total,
        "remaining_bills": remaining_bills,
        "bills_this_month": bills_this_month,
        "next_payday": next_payday,
        "pay_period_start": pay_period_start,
        "pay_period_end": pay_period_end,
        "days_until_payday": max(0, (next_payday - reference_date).days),
        "paycheck_amount": paycheck_amount,
        "remaining_paydays": remaining_paydays,
        "future_income_this_month": future_income_this_month,
        "available_monthly_balance": available_monthly_balance,
        "projected_end_of_month_balance": projected_end_of_month_balance,
        "current_pay_period_spending_total": current_pay_period_spending_total,
        "pay_period_remaining_recurring_total": pay_period_remaining_recurring_total,
        "pay_period_available_funds": pay_period_available_funds,
    }


def build_recurring_status_pie_data(snapshot: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"label": "Paid recurring bills", "value": max(snapshot["paid_recurring_bills_total"], 0.0)},
            {"label": "Remaining recurring bills", "value": max(snapshot["remaining_recurring_bills_total"], 0.0)},
        ]
    )


def build_pay_period_pie_data(snapshot: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"label": "Available funds", "value": max(snapshot["pay_period_available_funds"], 0.0)},
            {"label": "Remaining recurring bills (month)", "value": max(snapshot["pay_period_remaining_recurring_total"], 0.0)},
            {"label": "Current pay-period spend", "value": max(snapshot["current_pay_period_spending_total"], 0.0)},
        ]
    )


def evaluate_what_if(snapshot: dict[str, Any], hypothetical_amount: float) -> dict[str, Any]:
    spend_amount = max(0.0, _safe_float(hypothetical_amount))
    remaining_after_purchase = round(snapshot["current_available_balance"] - spend_amount, 2)
    after_remaining_bills_before_future_income = round(
        remaining_after_purchase - snapshot["remaining_recurring_bills_total"],
        2,
    )
    projected_after_purchase = round(snapshot["projected_end_of_month_balance"] - spend_amount, 2)

    # Bills are considered covered when cash on hand plus paychecks still due this
    # month can absorb the hypothetical purchase and the remaining recurring bills.
    covered_pool = remaining_after_purchase + snapshot["future_income_this_month"]
    bills_are_covered = covered_pool >= snapshot["remaining_recurring_bills_total"]

    return {
        "remaining_after_purchase": remaining_after_purchase,
        "after_remaining_bills_before_future_income": after_remaining_bills_before_future_income,
        "projected_after_purchase": projected_after_purchase,
        "bills_are_covered": bills_are_covered,
        "would_go_negative": remaining_after_purchase < 0 or projected_after_purchase < 0,
    }