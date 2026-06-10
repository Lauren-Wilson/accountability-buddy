from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile

import pandas as pd

from utils.budgeting import (
    DEFAULT_SETTINGS,
    EXPECTED_COLUMNS,
    SAMPLE_ROWS,
    auto_add_due_paychecks,
    build_budget_snapshot,
    load_recurring_bills,
    load_settings,
    load_transactions,
)


def seed_csvs(tmp_data_dir: Path) -> None:
    tmp_data_dir.mkdir(parents=True, exist_ok=True)
    for key, filename in {
        "transactions": "transactions.csv",
        "recurring_bills": "recurring_bills.csv",
        "liabilities": "liabilities.csv",
        "settings": "settings.csv",
    }.items():
        frame = pd.DataFrame(SAMPLE_ROWS[key], columns=EXPECTED_COLUMNS[key])
        frame.to_csv(tmp_data_dir / filename, index=False)


def set_settings(tmp_data_dir: Path) -> None:
    settings = DEFAULT_SETTINGS.copy()
    settings.update(
        {
            "known_payday": "2026-06-12",
            "pay_interval_days": "14",
            "paycheck_amount": "3067.68",
            "starting_available_cash": "87.97",
            "leftover_from_prior_month": "0",
            "starting_cash_as_of": "",
        }
    )
    frame = pd.DataFrame([{"setting": k, "value": v} for k, v in settings.items()])
    frame.to_csv(tmp_data_dir / "settings.csv", index=False)


def remove_sample_paycheck_for_day(tmp_data_dir: Path, target_day: str) -> None:
    tx = pd.read_csv(tmp_data_dir / "transactions.csv")
    tx["date"] = tx["date"].astype(str)
    tx["transaction_type"] = tx["transaction_type"].astype(str).str.lower()
    tx["category"] = tx["category"].astype(str).str.lower()
    tx = tx[
        ~(
            (tx["date"] == target_day)
            & (tx["transaction_type"] == "income")
            & (tx["category"] == "paycheck")
        )
    ]
    tx.to_csv(tmp_data_dir / "transactions.csv", index=False)


def count_paychecks_on_day(transactions: pd.DataFrame, day: date) -> int:
    tx = transactions.copy()
    tx["category"] = tx["category"].astype(str).str.lower()
    tx["transaction_type"] = tx["transaction_type"].astype(str).str.lower()
    return len(
        tx[
            (tx["date"].dt.date == day)
            & (tx["transaction_type"] == "income")
            & (tx["category"] == "paycheck")
        ]
    )


def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        seed_csvs(data_dir)
        set_settings(data_dir)

        # Ensure June 12 paycheck starts missing for this test.
        remove_sample_paycheck_for_day(data_dir, "2026-06-12")

        settings = load_settings(data_dir)
        recurring = load_recurring_bills(data_dir)

        # 1) Added on payday when missing.
        tx_before = load_transactions(data_dir)
        added = auto_add_due_paychecks(data_dir, tx_before, settings, date(2026, 6, 12))
        tx_after = load_transactions(data_dir)
        assert added == 1, f"Expected 1 paycheck added on 2026-06-12, got {added}"
        assert count_paychecks_on_day(tx_after, date(2026, 6, 12)) == 1, "Missing June 12 paycheck"

        # 2) Idempotent rerun (no duplicate).
        added_again = auto_add_due_paychecks(data_dir, tx_after, settings, date(2026, 6, 12))
        tx_after_again = load_transactions(data_dir)
        assert added_again == 0, f"Expected 0 added on second run, got {added_again}"
        assert count_paychecks_on_day(tx_after_again, date(2026, 6, 12)) == 1, "Duplicate June 12 paycheck"

        # 3) On June 13, next payday is June 26 and June 12 income is included.
        snapshot = build_budget_snapshot(tx_after_again, recurring, settings, date(2026, 6, 13))
        assert snapshot["next_payday"].isoformat() == "2026-06-26", snapshot["next_payday"].isoformat()
        june_12_income = tx_after_again[
            (tx_after_again["date"].dt.date == date(2026, 6, 12))
            & (tx_after_again["transaction_type"] == "income")
            & (tx_after_again["category"].str.lower() == "paycheck")
        ]["amount"].sum()
        assert june_12_income > 0, "June 12 income not present"

        # 4) No paycheck before payday.
        seed_csvs(data_dir)
        set_settings(data_dir)
        remove_sample_paycheck_for_day(data_dir, "2026-06-12")
        tx_pre = load_transactions(data_dir)
        added_pre = auto_add_due_paychecks(data_dir, tx_pre, settings, date(2026, 6, 11))
        assert added_pre == 0, f"Expected 0 before payday, got {added_pre}"

    print("PASS: auto paycheck scenarios verified")


if __name__ == "__main__":
    run()
