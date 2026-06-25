from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
import re
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
    "balance_snapshots": "balance_snapshots.csv",
    "bill_payment_log": "bill_payment_log.csv",
    "liability_payment_groups": "liability_payment_groups.csv",
    "liability_payment_log": "liability_payment_log.csv",
}

EXPECTED_COLUMNS = {
    "transactions": ["date", "amount", "category", "note", "transaction_type"],
    "recurring_bills": ["bill_id", "bill_name", "amount", "due_day", "category", "active", "split_group"],
    "liabilities": ["liability_id", "group_id", "name", "balance", "apr", "min_payment", "priority", "active"],
    "settings": ["setting", "value"],
    "balance_snapshots": ["date", "balance", "note"],
    "bill_payment_log": ["bill_id", "paid_date", "month", "amount_paid", "note"],
    "liability_payment_groups": ["group_id", "group_name", "payment_amount", "strategy", "active"],
    "liability_payment_log": [
        "payment_date",
        "payment_month",
        "group_id",
        "liability_id",
        "amount_applied",
        "interest_applied",
        "principal_applied",
        "starting_balance",
        "ending_balance",
        "note",
    ],
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
        {
            "bill_id": "mortgage_1",
            "bill_name": "Mortgage - First Half",
            "amount": 1243.44,
            "due_day": 10,
            "category": "Housing",
            "active": True,
            "split_group": "mortgage",
        },
        {
            "bill_id": "mortgage_2",
            "bill_name": "Mortgage - Second Half",
            "amount": 1243.44,
            "due_day": 24,
            "category": "Housing",
            "active": True,
            "split_group": "mortgage",
        },
        {
            "bill_id": "student_loan",
            "bill_name": "Student Loan",
            "amount": 1188.88,
            "due_day": 1,
            "category": "Debt",
            "active": True,
            "split_group": "",
        },
        {
            "bill_id": "utilities_old_house",
            "bill_name": "CPS Bill Old House",
            "amount": 76.16,
            "due_day": 8,
            "category": "Utilities",
            "active": True,
            "split_group": "",
        },
        {
            "bill_id": "utilities_new_house",
            "bill_name": "CPS Bill New House",
            "amount": 250.0,
            "due_day": 16,
            "category": "Utilities",
            "active": True,
            "split_group": "",
        },
        {
            "bill_id": "property_taxes",
            "bill_name": "Property Taxes",
            "amount": 294.0,
            "due_day": 27,
            "category": "Housing",
            "active": True,
            "split_group": "",
        },
    ],
    "liabilities": [
        {
            "liability_id": "loan_03",
            "group_id": "student_loans",
            "name": "Loan 03",
            "balance": 3552.26,
            "apr": 4.53,
            "min_payment": 57.49,
            "priority": 1,
            "active": True,
        },
        {
            "liability_id": "loan_04",
            "group_id": "student_loans",
            "name": "Loan 04",
            "balance": 0.0,
            "apr": 4.53,
            "min_payment": 0.0,
            "priority": 2,
            "active": False,
        },
        {
            "liability_id": "loan_05",
            "group_id": "student_loans",
            "name": "Loan 05",
            "balance": 15159.75,
            "apr": 4.3,
            "min_payment": 829.4,
            "priority": 3,
            "active": True,
        },
    ],
    "settings": [{"setting": key, "value": value} for key, value in DEFAULT_SETTINGS.items()],
    "balance_snapshots": [],
    "bill_payment_log": [],
    "liability_payment_groups": [
        {
            "group_id": "student_loans",
            "group_name": "Student Loans",
            "payment_amount": 1188.88,
            "strategy": "avalanche",
            "active": True,
        },
    ],
    "liability_payment_log": [],
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


def _parse_optional_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


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
    if "bill_id" not in bills.columns:
        bills["bill_id"] = ""
    if "bill_name" not in bills.columns:
        bills["bill_name"] = ""
    if "category" not in bills.columns:
        bills["category"] = ""
    if "split_group" not in bills.columns:
        bills["split_group"] = ""

    def _to_bill_id(row: pd.Series) -> str:
        existing = str(row.get("bill_id", "")).strip().lower()
        if existing:
            return existing
        bill_name = str(row.get("bill_name", "")).strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "_", bill_name).strip("_")
        return normalized or "unnamed_bill"

    bills["amount"] = pd.to_numeric(bills["amount"], errors="coerce").fillna(0.0)
    bills["due_day"] = pd.to_numeric(bills["due_day"], errors="coerce").fillna(1).astype(int)
    bills["active"] = bills["active"].apply(lambda value: _safe_bool(value, True))
    bills["bill_id"] = bills.apply(_to_bill_id, axis=1)
    bills["bill_name"] = bills["bill_name"].fillna("Unnamed Bill").astype(str).str.strip()
    bills["category"] = bills["category"].fillna("Bills").astype(str).str.strip()
    bills["split_group"] = bills["split_group"].fillna("").astype(str).str.strip().str.lower()
    bills.loc[bills["bill_name"] == "", "bill_name"] = "Unnamed Bill"
    bills.loc[bills["category"] == "", "category"] = "Bills"
    return bills[EXPECTED_COLUMNS["recurring_bills"]].copy()


def load_balance_snapshots(data_dir: Path) -> pd.DataFrame:
    snapshots = _read_csv(data_dir, "balance_snapshots")
    snapshots["date"] = snapshots["date"].apply(_parse_optional_date)
    snapshots["date"] = pd.to_datetime(snapshots["date"], errors="coerce")
    snapshots["balance"] = pd.to_numeric(snapshots["balance"], errors="coerce")
    snapshots["note"] = snapshots["note"].fillna("").astype(str).str.strip()
    snapshots = snapshots.dropna(subset=["date", "balance"]).sort_values("date", kind="mergesort")
    return snapshots


def load_bill_payment_log(data_dir: Path) -> pd.DataFrame:
    payment_log = _read_csv(data_dir, "bill_payment_log")
    payment_log["bill_id"] = payment_log["bill_id"].fillna("").astype(str).str.strip().str.lower()
    payment_log["paid_date"] = pd.to_datetime(payment_log["paid_date"], errors="coerce")
    payment_log["month"] = payment_log["month"].fillna("").astype(str).str.strip()
    payment_log["amount_paid"] = pd.to_numeric(payment_log["amount_paid"], errors="coerce").fillna(0.0)
    payment_log["note"] = payment_log["note"].fillna("").astype(str).str.strip()
    payment_log = payment_log.dropna(subset=["paid_date"])
    return payment_log


def load_liabilities(data_dir: Path) -> pd.DataFrame:
    liabilities = _read_csv(data_dir, "liabilities")
    normalized_columns = {str(col).strip().lower(): col for col in liabilities.columns}

    if "liability_id" not in liabilities.columns:
        liabilities["liability_id"] = ""
    if "group_id" not in liabilities.columns:
        liabilities["group_id"] = ""
    if "name" not in liabilities.columns:
        liabilities["name"] = "Unnamed Liability"
    if "min_payment" not in liabilities.columns:
        if "current_payment" in liabilities.columns:
            liabilities["min_payment"] = liabilities["current_payment"]
        else:
            liabilities["min_payment"] = 0.0
    if "priority" not in liabilities.columns:
        liabilities["priority"] = 999
    if "active" not in liabilities.columns:
        liabilities["active"] = True

    liabilities["name"] = liabilities["name"].fillna("Unnamed Liability").astype(str).str.strip()
    liabilities.loc[liabilities["name"] == "", "name"] = "Unnamed Liability"
    liabilities["liability_id"] = liabilities["liability_id"].fillna("").astype(str).str.strip().str.lower()
    liabilities["group_id"] = liabilities["group_id"].fillna("").astype(str).str.strip().str.lower()
    liabilities["balance"] = pd.to_numeric(liabilities["balance"], errors="coerce").fillna(0.0)
    liabilities["apr"] = pd.to_numeric(liabilities["apr"], errors="coerce").fillna(0.0)
    liabilities["min_payment"] = pd.to_numeric(liabilities["min_payment"], errors="coerce").fillna(0.0)
    liabilities["priority"] = pd.to_numeric(liabilities["priority"], errors="coerce").fillna(999).astype(int)
    liabilities["active"] = liabilities["active"].apply(lambda value: _safe_bool(value, True))

    def _fallback_liability_id(row: pd.Series) -> str:
        existing = str(row.get("liability_id", "")).strip().lower()
        if existing:
            return existing
        generated = re.sub(r"[^a-z0-9]+", "_", str(row.get("name", "")).strip().lower()).strip("_")
        return generated or "unnamed_liability"

    liabilities["liability_id"] = liabilities.apply(_fallback_liability_id, axis=1)
    liabilities.loc[liabilities["group_id"] == "", "group_id"] = "default"
    liabilities.loc[liabilities["balance"] <= 0, "active"] = False
    return liabilities[EXPECTED_COLUMNS["liabilities"]].copy()


def load_liability_payment_groups(
    data_dir: Path,
    liabilities: pd.DataFrame | None = None,
    **_kwargs: Any,
) -> pd.DataFrame:
    groups = _read_csv(data_dir, "liability_payment_groups")
    if "group_name" not in groups.columns:
        groups["group_name"] = ""
    if "strategy" not in groups.columns:
        groups["strategy"] = "avalanche"
    if "active" not in groups.columns:
        groups["active"] = True

    groups["group_id"] = groups["group_id"].fillna("").astype(str).str.strip().str.lower()
    groups["group_name"] = groups["group_name"].fillna("").astype(str).str.strip()
    groups["payment_amount"] = pd.to_numeric(groups["payment_amount"], errors="coerce").fillna(0.0)
    groups["strategy"] = groups["strategy"].fillna("priority").astype(str).str.strip().str.lower()
    groups["active"] = groups["active"].apply(lambda value: _safe_bool(value, True))
    groups.loc[groups["group_name"] == "", "group_name"] = groups["group_id"]
    groups = groups[groups["group_id"] != ""]

    # Product rule: all liability groups use priority strategy.
    groups["strategy"] = "priority"

    if liabilities is not None and not liabilities.empty:
        liability_groups = liabilities.copy()
        liability_groups["group_id"] = liability_groups["group_id"].fillna("").astype(str).str.strip().str.lower()
        liability_groups["name"] = liability_groups["name"].fillna("").astype(str).str.strip()
        liability_groups["min_payment"] = pd.to_numeric(liability_groups.get("min_payment", 0.0), errors="coerce").fillna(0.0)
        liability_groups = liability_groups[liability_groups["group_id"] != ""]

        existing_group_ids = set(groups["group_id"].tolist())
        inferred_rows: list[dict[str, Any]] = []
        for group_id, frame in liability_groups.groupby("group_id"):
            if group_id in existing_group_ids:
                continue
            inferred_rows.append(
                {
                    "group_id": group_id,
                    "group_name": group_id.replace("_", " ").title(),
                    "payment_amount": round(float(frame["min_payment"].sum()), 2),
                    "strategy": "priority",
                    "active": True,
                }
            )

        if inferred_rows:
            groups = pd.concat(
                [groups, pd.DataFrame(inferred_rows, columns=EXPECTED_COLUMNS["liability_payment_groups"])],
                ignore_index=True,
            )

    return groups[EXPECTED_COLUMNS["liability_payment_groups"]].copy()


def load_liability_payment_log(data_dir: Path) -> pd.DataFrame:
    payment_log = _read_csv(data_dir, "liability_payment_log")
    payment_log["payment_date"] = pd.to_datetime(payment_log["payment_date"], errors="coerce")
    payment_log["payment_month"] = payment_log["payment_month"].fillna("").astype(str).str.strip()
    payment_log["group_id"] = payment_log["group_id"].fillna("").astype(str).str.strip().str.lower()
    payment_log["liability_id"] = payment_log["liability_id"].fillna("").astype(str).str.strip().str.lower()
    for col in ["amount_applied", "interest_applied", "principal_applied", "starting_balance", "ending_balance"]:
        payment_log[col] = pd.to_numeric(payment_log[col], errors="coerce").fillna(0.0)
    payment_log["note"] = payment_log["note"].fillna("").astype(str).str.strip()
    payment_log = payment_log.dropna(subset=["payment_date"])
    return payment_log[EXPECTED_COLUMNS["liability_payment_log"]].copy()


def save_liabilities(data_dir: Path, liabilities: pd.DataFrame) -> None:
    frame = liabilities.copy()
    for column in EXPECTED_COLUMNS["liabilities"]:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[EXPECTED_COLUMNS["liabilities"]]

    if gsheets.is_configured():
        gsheets.write_sheet("liabilities", frame, EXPECTED_COLUMNS["liabilities"])
        return

    path = data_dir / DATA_FILES["liabilities"]
    frame.to_csv(path, index=False)


def append_liability_payment_log_rows(data_dir: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    normalized_rows = []
    for row in rows:
        normalized_rows.append(
            {
                "payment_date": row.get("payment_date", date.today().isoformat()),
                "payment_month": str(row.get("payment_month", "")).strip(),
                "group_id": str(row.get("group_id", "")).strip().lower(),
                "liability_id": str(row.get("liability_id", "")).strip().lower(),
                "amount_applied": _safe_float(row.get("amount_applied", 0.0)),
                "interest_applied": _safe_float(row.get("interest_applied", 0.0)),
                "principal_applied": _safe_float(row.get("principal_applied", 0.0)),
                "starting_balance": _safe_float(row.get("starting_balance", 0.0)),
                "ending_balance": _safe_float(row.get("ending_balance", 0.0)),
                "note": str(row.get("note", "")).strip(),
            }
        )

    if gsheets.is_configured():
        for row in normalized_rows:
            gsheets.append_row("liability_payment_log", row, EXPECTED_COLUMNS["liability_payment_log"])
        return

    path = data_dir / DATA_FILES["liability_payment_log"]
    payment_log = _read_csv(data_dir, "liability_payment_log")
    next_rows = pd.DataFrame(normalized_rows, columns=EXPECTED_COLUMNS["liability_payment_log"])
    combined = pd.concat([payment_log, next_rows], ignore_index=True)
    combined.to_csv(path, index=False)


def has_group_payment_for_month(
    payment_log: pd.DataFrame,
    group_id: str,
    payment_month: str,
) -> bool:
    if payment_log.empty:
        return False

    gid = str(group_id).strip().lower()
    month_key = str(payment_month).strip()
    if not gid or not month_key:
        return False

    return not payment_log[
        (payment_log["group_id"].astype(str).str.strip().str.lower() == gid)
        & (payment_log["payment_month"].astype(str).str.strip() == month_key)
    ].empty


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
    _ = transactions
    _ = reference_date
    derived = liabilities.copy()
    if derived.empty:
        derived["original_balance"] = pd.Series(dtype="float64")
        derived["paid_to_date"] = pd.Series(dtype="float64")
        derived["current_balance"] = pd.Series(dtype="float64")
        return derived

    derived["balance"] = pd.to_numeric(derived["balance"], errors="coerce").fillna(0.0)
    derived["original_balance"] = derived["balance"]
    derived["paid_to_date"] = 0.0
    derived["current_balance"] = derived["balance"].clip(lower=0.0)
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


def append_balance_snapshot(data_dir: Path, row: dict[str, Any]) -> None:
    snapshot_date = str(row.get("date", "")).strip()
    snapshot_date_obj = _parse_date(snapshot_date)
    normalized_row = {
        "date": snapshot_date_obj.isoformat(),
        "balance": _safe_float(row.get("balance", 0.0)),
        "note": str(row.get("note", "")).strip(),
    }

    snapshots = _read_csv(data_dir, "balance_snapshots")
    if "date" not in snapshots.columns:
        snapshots["date"] = ""

    # Upsert by date so correcting today's balance replaces stale entry.
    snapshot_dates = snapshots["date"].apply(_parse_optional_date)
    same_day_mask = snapshot_dates == snapshot_date_obj
    snapshots = snapshots[~same_day_mask.fillna(False)]
    next_row = pd.DataFrame([normalized_row], columns=EXPECTED_COLUMNS["balance_snapshots"])
    combined = pd.concat([snapshots, next_row], ignore_index=True)

    if gsheets.is_configured():
        gsheets.write_sheet("balance_snapshots", combined, EXPECTED_COLUMNS["balance_snapshots"])
        return

    path = data_dir / DATA_FILES["balance_snapshots"]
    combined.to_csv(path, index=False)


def append_bill_payment_log(data_dir: Path, row: dict[str, Any]) -> bool:
    bill_id = str(row.get("bill_id", "")).strip().lower()
    month = str(row.get("month", "")).strip()
    if not bill_id or not month:
        return False

    existing = load_bill_payment_log(data_dir)
    already_logged = not existing[
        (existing["bill_id"] == bill_id)
        & (existing["month"] == month)
    ].empty
    if already_logged:
        return False

    normalized_row = {
        "bill_id": bill_id,
        "paid_date": row.get("paid_date", date.today().isoformat()),
        "month": month,
        "amount_paid": _safe_float(row.get("amount_paid", 0.0)),
        "note": str(row.get("note", "")).strip(),
    }

    if gsheets.is_configured():
        gsheets.append_row("bill_payment_log", normalized_row, EXPECTED_COLUMNS["bill_payment_log"])
        return True

    path = data_dir / DATA_FILES["bill_payment_log"]
    payment_log = _read_csv(data_dir, "bill_payment_log")
    next_row = pd.DataFrame([normalized_row], columns=EXPECTED_COLUMNS["bill_payment_log"])
    combined = pd.concat([payment_log, next_row], ignore_index=True)
    combined.to_csv(path, index=False)
    return True


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


def _latest_balance_snapshot(balance_snapshots: pd.DataFrame, reference_date: date) -> dict[str, Any] | None:
    if balance_snapshots.empty:
        return None

    snapshots = balance_snapshots.copy()
    snapshots["date"] = pd.to_datetime(snapshots["date"], errors="coerce")
    snapshots["balance"] = pd.to_numeric(snapshots["balance"], errors="coerce")
    snapshots = snapshots.dropna(subset=["date", "balance"])
    snapshots = snapshots[snapshots["date"].dt.date <= reference_date]
    if snapshots.empty:
        return None

    # If multiple rows share the same date, use the last entered row for that day.
    max_date = snapshots["date"].max()
    same_day = snapshots[snapshots["date"] == max_date]
    latest = same_day.iloc[-1]
    return {
        "date": latest["date"].date(),
        "balance": round(_safe_float(latest["balance"], 0.0), 2),
        "note": str(latest.get("note", "")).strip(),
    }


def build_budget_snapshot(
    transactions: pd.DataFrame,
    recurring_bills: pd.DataFrame,
    settings: dict[str, str],
    reference_date: date,
    balance_snapshots: pd.DataFrame | None = None,
    bill_payment_log: pd.DataFrame | None = None,
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
    calculated_available_balance = round(starting_cash + through_today["signed_amount"].sum(), 2)

    snapshots = balance_snapshots if balance_snapshots is not None else pd.DataFrame(columns=EXPECTED_COLUMNS["balance_snapshots"])
    latest_snapshot = _latest_balance_snapshot(snapshots, reference_date)
    if latest_snapshot is not None:
        current_available_balance = latest_snapshot["balance"]
        last_balance_update_date = latest_snapshot["date"]
    else:
        current_available_balance = calculated_available_balance
        last_balance_update_date = None

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

    log = bill_payment_log if bill_payment_log is not None else pd.DataFrame(columns=EXPECTED_COLUMNS["bill_payment_log"])
    log = log.copy()
    if not log.empty:
        log["bill_id"] = log["bill_id"].fillna("").astype(str).str.strip().str.lower()
        log["month"] = log["month"].fillna("").astype(str).str.strip()
    month_key = reference_date.strftime("%Y-%m")
    paid_bill_ids = set(log.loc[log["month"] == month_key, "bill_id"].tolist())
    bills_this_month["is_paid"] = bills_this_month["bill_id"].astype(str).str.strip().str.lower().isin(paid_bill_ids)

    paid_bills = bills_this_month[bills_this_month["is_paid"]]
    remaining_bills = bills_this_month[~bills_this_month["is_paid"]]
    paid_recurring_bills_total = round(paid_bills["amount"].sum(), 2)
    remaining_recurring_bills_total = round(remaining_bills["amount"].sum(), 2)

    next_payday = get_next_payday(reference_date, anchor_payday, pay_interval_days)
    pay_period_start, pay_period_end = get_current_pay_period(reference_date, anchor_payday, pay_interval_days)
    remaining_paydays = get_remaining_paydays_in_month(reference_date, anchor_payday, pay_interval_days)
    future_income_this_month = round(len(remaining_paydays) * paycheck_amount, 2)

    projected_end_of_month_balance = round(current_available_balance - remaining_recurring_bills_total, 2)
    remaining_after_unpaid_bills = projected_end_of_month_balance

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
        "calculated_available_balance": calculated_available_balance,
        "last_balance_update_date": last_balance_update_date,
        "has_manual_balance_snapshot": last_balance_update_date is not None,
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
        "remaining_after_unpaid_bills": remaining_after_unpaid_bills,
        "month_key": month_key,
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
            {"label": "Paid recurring bills", "value": max(snapshot["paid_recurring_bills_total"], 0.0), "pull": 0.0},
            {"label": "Unpaid recurring bills", "value": max(snapshot["remaining_recurring_bills_total"], 0.0), "pull": 0.0},
            {"label": "Manual spending", "value": max(snapshot["manual_spending_total"], 0.0), "pull": 0.0},
            {"label": "Remaining after unpaid bills", "value": max(snapshot["remaining_after_unpaid_bills"], 0.0), "pull": 0.1},
            {"label": "Leftover from prior month", "value": max(snapshot["leftover_slice"], 0.0), "pull": 0.12},
        ]
    )


def evaluate_what_if(snapshot: dict[str, Any], hypothetical_amount: float) -> dict[str, Any]:
    spend_amount = max(0.0, _safe_float(hypothetical_amount))
    remaining_after_purchase = round(snapshot["current_available_balance"] - spend_amount, 2)
    after_remaining_bills_before_future_income = round(
        remaining_after_purchase - snapshot["remaining_recurring_bills_total"],
        2,
    )
    projected_after_purchase = after_remaining_bills_before_future_income

    bills_are_covered = projected_after_purchase >= 0

    return {
        "remaining_after_purchase": remaining_after_purchase,
        "after_remaining_bills_before_future_income": after_remaining_bills_before_future_income,
        "projected_after_purchase": projected_after_purchase,
        "unpaid_bills_remaining": round(snapshot["remaining_recurring_bills_total"], 2),
        "hypothetical_spend": spend_amount,
        "bills_are_covered": bills_are_covered,
        "would_go_negative": remaining_after_purchase < 0 or projected_after_purchase < 0,
    }