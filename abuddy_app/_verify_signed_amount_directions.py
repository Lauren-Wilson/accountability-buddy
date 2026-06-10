from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from utils.budgeting import (
    DEFAULT_SETTINGS,
    EXPECTED_COLUMNS,
    SAMPLE_ROWS,
    build_budget_snapshot,
    load_recurring_bills,
    load_transactions,
)
from utils import gsheets


def run() -> None:
    # Keep this verification deterministic by forcing local CSV mode.
    gsheets.is_configured = lambda: False  # type: ignore[assignment]

    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        data_dir.mkdir(parents=True, exist_ok=True)

        for key, filename in {
            "transactions": "transactions.csv",
            "recurring_bills": "recurring_bills.csv",
            "liabilities": "liabilities.csv",
            "settings": "settings.csv",
        }.items():
            pd.DataFrame(SAMPLE_ROWS[key], columns=EXPECTED_COLUMNS[key]).to_csv(data_dir / filename, index=False)

        settings = DEFAULT_SETTINGS.copy()
        settings.update(
            {
                "known_payday": "2026-06-12",
                "pay_interval_days": "14",
                "paycheck_amount": "1000",
                "starting_available_cash": "1000",
                "starting_cash_as_of": "2026-06-01",
                "leftover_from_prior_month": "0",
            }
        )

        tx = load_transactions(data_dir)
        recurring = load_recurring_bills(data_dir)
        base = build_budget_snapshot(tx, recurring, settings, date(2026, 6, 13))["current_available_balance"]

        def with_tx(amount: float, category: str, transaction_type: str) -> float:
            temp = tx.copy()
            temp = pd.concat(
                [
                    temp,
                    pd.DataFrame(
                        [
                            {
                                "date": pd.to_datetime("2026-06-13"),
                                "amount": amount,
                                "category": category,
                                "note": "test",
                                "transaction_type": transaction_type,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
            return build_budget_snapshot(temp, recurring, settings, date(2026, 6, 13))["current_available_balance"]

        income_balance = with_tx(50.0, "Paycheck", "income")
        spend_balance = with_tx(50.0, "General", "spend")
        debt_balance = with_tx(50.0, "Visa Card", "debt_payment")

        assert round(income_balance - base, 2) == 50.00, f"Income should increase by 50, got {income_balance - base:.2f}"
        assert round(spend_balance - base, 2) == -50.00, f"Spend should decrease by 50, got {spend_balance - base:.2f}"
        assert round(debt_balance - base, 2) == -50.00, f"Debt payment should decrease by 50, got {debt_balance - base:.2f}"

        # Regression: when known payday is in the future and starting_cash_as_of is
        # blank, transactions dated today must still affect current_available_balance.
        future_anchor_settings = settings.copy()
        future_anchor_settings.update(
            {
                "known_payday": "2026-06-20",
                "starting_cash_as_of": "",
                "starting_available_cash": "1000",
            }
        )

        future_ref = date(2026, 6, 10)
        future_anchor_base = build_budget_snapshot(tx, recurring, future_anchor_settings, future_ref)[
            "current_available_balance"
        ]

        future_spend_tx = pd.concat(
            [
                tx.copy(),
                pd.DataFrame(
                    [
                        {
                            "date": pd.to_datetime("2026-06-10"),
                            "amount": 40.0,
                            "category": "General",
                            "note": "future-anchor spend test",
                            "transaction_type": "spend",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        future_spend_balance = build_budget_snapshot(
            future_spend_tx,
            recurring,
            future_anchor_settings,
            future_ref,
        )["current_available_balance"]

        assert round(future_spend_balance - future_anchor_base, 2) == -40.00, (
            "Same-day spend should reduce balance by 40 even with future known_payday, "
            f"got {future_spend_balance - future_anchor_base:.2f}"
        )

    print("PASS: signed amount direction checks verified")


if __name__ == "__main__":
    run()
