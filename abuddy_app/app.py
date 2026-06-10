from __future__ import annotations

from datetime import date
from pathlib import Path
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.budgeting import (
    auto_add_due_paychecks,
    append_transaction,
    build_pay_period_pie_data,
    build_budget_snapshot,
    build_recurring_status_pie_data,
    ensure_data_files,
    evaluate_what_if,
    get_category_options,
    load_liabilities,
    load_recurring_bills,
    load_settings,
    load_transactions,
)
from utils.debt_math import compare_payment_scenarios
from utils import gsheets

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

st.set_page_config(
    page_title="Accountability Buddy",
    page_icon="💸",
    layout="centered",
    initial_sidebar_state="expanded",
)


def inject_theme() -> None:
    st.markdown(
        """
        <style>
            :root {
                --abuddy-red: #d72638;
                --abuddy-yellow: #ffc72c;
                --abuddy-cream: #fff7e8;
                --abuddy-brown: #5c2b14;
                --abuddy-green: #1b8f5a;
            }
            .stApp {
                background: linear-gradient(180deg, var(--abuddy-cream) 0%, #fffaf1 100%);
                color: #2b1d12;
            }
            .block-container {
                padding-top: 3.5rem;
                padding-bottom: 4rem;
                max-width: 760px;
            }
            .abuddy-hero {
                position: fixed;
                top: 3.2rem;
                right: 0.8rem;
                z-index: 10;
                width: 84px;
                height: 84px;
                opacity: 0.96;
                pointer-events: none;
            }
            .abuddy-card {
                background: rgba(255,255,255,0.92);
                border: 2px solid rgba(215, 38, 56, 0.14);
                border-radius: 20px;
                padding: 1rem;
                box-shadow: 0 12px 28px rgba(92, 43, 20, 0.08);
                margin-bottom: 0.85rem;
            }
            .abuddy-title {
                font-size: 1.95rem;
                font-weight: 800;
                color: var(--abuddy-red);
                margin-bottom: 0.2rem;
            }
            .abuddy-subtitle {
                color: #6d4c3d;
                margin-bottom: 1rem;
            }
            .abuddy-chip {
                display: inline-block;
                padding: 0.3rem 0.65rem;
                border-radius: 999px;
                background: rgba(255, 199, 44, 0.22);
                color: var(--abuddy-brown);
                font-size: 0.9rem;
                font-weight: 700;
                margin-bottom: 0.75rem;
            }
            .stButton > button {
                width: 100%;
                border-radius: 999px;
                border: none;
                min-height: 3rem;
                font-weight: 800;
            }
            .stFormSubmitButton > button {
                width: 100%;
                min-height: 3.1rem;
                border-radius: 999px;
                font-weight: 800;
            }
            .abuddy-positive {
                color: var(--abuddy-green);
                font-weight: 700;
            }
            .abuddy-warning {
                color: var(--abuddy-red);
                font-weight: 700;
            }
            @media (max-width: 640px) {
                .abuddy-hero {
                    width: 62px;
                    height: 62px;
                    top: 4.4rem;
                    right: 0.45rem;
                }
                .block-container {
                    padding-top: 4.9rem;
                }
            }
        </style>
        <div class="abuddy-hero" aria-hidden="true">
            <svg viewBox="0 0 120 120" width="100%" height="100%">
                <ellipse cx="60" cy="58" rx="34" ry="30" fill="#ffc72c" stroke="#d72638" stroke-width="5" />
                <circle cx="49" cy="52" r="4.8" fill="#2b1d12" />
                <circle cx="71" cy="52" r="4.8" fill="#2b1d12" />
                <path d="M47 68 Q60 78 73 68" fill="none" stroke="#2b1d12" stroke-width="4" stroke-linecap="round" />
                <circle cx="40" cy="64" r="4" fill="#ff9aa8" opacity="0.65" />
                <circle cx="80" cy="64" r="4" fill="#ff9aa8" opacity="0.65" />
                <path d="M26 60 L12 46" stroke="#2b1d12" stroke-width="4" stroke-linecap="round" />
                <path d="M94 60 L108 46" stroke="#2b1d12" stroke-width="4" stroke-linecap="round" />
                <ellipse cx="39" cy="107" rx="12" ry="7" fill="#d72638" />
                <ellipse cx="81" cy="107" rx="12" ry="7" fill="#d72638" />
                <path d="M36 28 Q48 14 59 24" fill="none" stroke="#d72638" stroke-width="5" stroke-linecap="round" />
            </svg>
        </div>
        """,
        unsafe_allow_html=True,
    )


def money(value: float) -> str:
    return f"${value:,.2f}"


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_amount_input(raw_value: str) -> float | None:
    value = str(raw_value or "").strip()
    if not value:
        return None

    normalized = re.sub(r"[,$\s]", "", value)
    if not normalized:
        return None

    if "." in normalized:
        try:
            parsed = float(normalized)
            return max(parsed, 0.0)
        except ValueError:
            return None

    digits_only = re.sub(r"\D", "", normalized)
    if not digits_only:
        return None

    return int(digits_only) / 100.0


def _apply_keypad_token(current_value: str, token: str) -> str:
    current = str(current_value or "")
    if token == "⌫":
        return current[:-1]
    if token == "C":
        return ""
    if token == ".":
        if "." in current:
            return current
        return "0." if not current else f"{current}."
    if token.isdigit():
        return f"{current}{token}"
    return current


def _queue_keypad_token(field_key: str, token: str) -> None:
    st.session_state[f"{field_key}__pending_token"] = token


def _apply_queued_keypad_token(field_key: str) -> None:
    pending_key = f"{field_key}__pending_token"
    token = st.session_state.get(pending_key)
    if token is None:
        return

    current_value = str(st.session_state.get(field_key, ""))
    st.session_state[field_key] = _apply_keypad_token(current_value, str(token))
    st.session_state[pending_key] = None


def render_amount_keypad(field_key: str, keypad_key_prefix: str) -> None:
    if field_key not in st.session_state:
        st.session_state[field_key] = ""

    st.caption("Tap keypad")
    keypad_rows = [
        ["1", "2", "3"],
        ["4", "5", "6"],
        ["7", "8", "9"],
        ["C", "0", "⌫"],
        ["."],
    ]

    for row_index, row in enumerate(keypad_rows):
        columns = st.columns(len(row))
        for column_index, token in enumerate(row):
            columns[column_index].button(
                token,
                key=f"{keypad_key_prefix}_keypad_{row_index}_{column_index}",
                use_container_width=True,
                on_click=_queue_keypad_token,
                args=(field_key, token),
            )


def pie_figure(pie_data: pd.DataFrame, colors: list[str]) -> go.Figure:
    figure = go.Figure(
        data=[
            go.Pie(
                labels=pie_data["label"],
                values=pie_data["value"],
                hole=0.0,
                sort=False,
                marker={"colors": colors},
                textinfo="label+percent",
                hovertemplate="%{label}<br>%{value:$,.2f}<extra></extra>",
            )
        ]
    )
    figure.update_layout(
        margin=dict(l=8, r=8, t=12, b=8),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        font={"color": "#2b1d12", "size": 13},
    )
    return figure


def render_header(snapshot: dict) -> None:
    st.markdown('<div class="abuddy-card">', unsafe_allow_html=True)
    st.markdown('<div class="abuddy-title">Accountability Buddy</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="abuddy-subtitle">A-Buddy helps you decide what you can safely spend on {snapshot["reference_date"].isoformat()}.</div>',
        unsafe_allow_html=True,
    )
    st.markdown('</div>', unsafe_allow_html=True)


def render_metrics(snapshot: dict) -> None:
    col1, col2 = st.columns(2)
    col3, col4 = st.columns(2)
    col1.metric("Current available balance", money(snapshot["current_available_balance"]))
    col2.metric("Next payday", snapshot["next_payday"].strftime("%a, %b %d"))
    col3.metric("Days until payday", str(snapshot["days_until_payday"]))
    col4.metric("Projected end-of-month", money(snapshot["projected_end_of_month_balance"]))
    st.caption(
        "Projected end-of-month includes remaining paychecks in this month and subtracts remaining recurring bills."
    )


def render_budget_pies(snapshot: dict) -> None:
    st.subheader("Recurring bills this month")
    st.caption("Calendar-month status: bills are marked paid when a matching transaction exists this month.")
    st.plotly_chart(
        pie_figure(build_recurring_status_pie_data(snapshot), ["#2f9e44", "#d72638"]),
        use_container_width=True,
    )

    st.subheader("Current pay period")
    st.caption(
        f"{snapshot['pay_period_start'].isoformat()} to {snapshot['pay_period_end'].isoformat()}: available cash, remaining recurring bills for this month, and non-recurring spend so far."
    )
    st.plotly_chart(
        pie_figure(build_pay_period_pie_data(snapshot), ["#ffc72c", "#d72638", "#2f9e44"]),
        use_container_width=True,
    )


def render_transaction_forms(category_options: list[str], effective_today: date) -> None:
    st.subheader("Quick cash moves")

    entry_date = st.date_input("Date", value=effective_today, key="quick_date")
    _apply_queued_keypad_token("quick_amount_text")
    amount_input = st.text_input(
        "Amount (type digits, e.g. 123456 → $1,234.56)",
        value="",
        key="quick_amount_text",
        placeholder="0",
    )
    render_amount_keypad("quick_amount_text", "quick")

    parsed_amount = parse_amount_input(amount_input)
    if parsed_amount is None:
        st.caption("Amount preview: —")
    else:
        st.caption(f"Amount preview: **{money(parsed_amount)}**")

    default_category = "Uncategorized"
    default_transaction_type = "spend"
    category_suggestions = ["Uncategorized", "General", "Paycheck", "Income", "Reversal"]

    details_categories = []
    for option in category_suggestions + category_options:
        if option and option not in details_categories:
            details_categories.append(option)

    with st.expander("Details"):
        st.selectbox(
            "Transaction type",
            ["spend", "debt_payment", "adjustment", "income"],
            index=0,
            key="quick_transaction_type",
            help="Transaction type controls whether this amount adds to or subtracts from available balance.",
        )
        st.selectbox(
            "Category",
            options=details_categories,
            index=0,
            key="quick_category",
        )
        st.text_input("Note", value="", key="quick_note")

    submitted = st.button("Save transaction", use_container_width=True, key="quick_save_transaction")
    if submitted:
        if parsed_amount is None or parsed_amount <= 0:
            st.error("Enter a valid amount greater than $0.00.")
            return

        chosen_transaction_type = st.session_state.get("quick_transaction_type", default_transaction_type)
        chosen_category = st.session_state.get("quick_category", default_category)
        chosen_note = st.session_state.get("quick_note", "")

        append_transaction(
            DATA_DIR,
            {
                "date": entry_date.isoformat(),
                "amount": parsed_amount,
                "category": chosen_category or default_category,
                "note": chosen_note,
                "transaction_type": chosen_transaction_type or default_transaction_type,
            },
        )
        st.session_state["quick_amount_text__pending_token"] = "C"
        st.success("Transaction saved.")
        st.rerun()


def render_forecast(snapshot: dict) -> None:
    st.subheader("What if I spend…")
    _apply_queued_keypad_token("what_if_amount_text")
    amount_input = st.text_input(
        "Spend amount now (type digits, e.g. 20000 → $200.00)",
        value="",
        key="what_if_amount_text",
        placeholder="0",
    )
    render_amount_keypad("what_if_amount_text", "what_if")
    parsed_amount = parse_amount_input(amount_input)
    if parsed_amount is None:
        st.caption("Amount preview: —")
    else:
        st.caption(f"Amount preview: **{money(parsed_amount)}**")
    submitted = st.button("Run forecast", use_container_width=True, key="run_forecast")

    if submitted:
        if parsed_amount is None or parsed_amount <= 0:
            st.error("Enter a valid amount greater than $0.00.")
            return

        result = evaluate_what_if(snapshot, parsed_amount)
        after_remaining_bills_before_future_income = result.get(
            "after_remaining_bills_before_future_income",
            round(result["remaining_after_purchase"] - snapshot["remaining_recurring_bills_total"], 2),
        )
        st.markdown('<div class="abuddy-card">', unsafe_allow_html=True)
        st.write(f"Cash after this spend today: **{money(result['remaining_after_purchase'])}**")
        st.write(
            f"Cash after this spend and remaining recurring bills (before future paychecks): **{money(after_remaining_bills_before_future_income)}**"
        )
        st.write(
            f"Projected end-of-month after purchase (remaining pay periods this month): **{money(result['projected_after_purchase'])}**"
        )
        st.write(
            "Upcoming bills covered: **Yes**"
            if result["bills_are_covered"]
            else "Upcoming bills covered: **No**"
        )
        if result["would_go_negative"]:
            st.warning("This purchase would push your cash-based plan negative.")
        else:
            st.success("This purchase stays inside your current cash-based plan.")
        st.markdown('</div>', unsafe_allow_html=True)


def render_bills(snapshot: dict) -> None:
    st.subheader("Recurring bills this month")
    if snapshot["bills_this_month"].empty:
        st.info("No active recurring bills found.")
        return

    display = snapshot["bills_this_month"][["bill_name", "amount", "due_date", "category"]].copy()
    display["amount"] = display["amount"].map(money)
    display["due_date"] = display["due_date"].astype(str)
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.caption(
        f"Paid recurring bills this month: {money(snapshot['paid_recurring_bills_total'])} · Remaining recurring bills this month: {money(snapshot['remaining_recurring_bills_total'])}"
    )


def render_recent_transactions(transactions: pd.DataFrame) -> None:
    st.subheader("Recent transactions")
    if transactions.empty:
        st.info("No transactions yet.")
        return

    display = transactions.sort_values("date", ascending=False).head(8).copy()
    display["date"] = display["date"].dt.strftime("%Y-%m-%d")
    display["amount"] = display["amount"].map(money)
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_sidebar(settings: dict, effective_today: date, liabilities: pd.DataFrame) -> date:
    st.sidebar.header("Planner settings")

    # --- data source indicator ---
    if gsheets.is_configured():
        sheet_url = gsheets.spreadsheet_url()
        if sheet_url:
            st.sidebar.success(f"[Google Sheets connected]({sheet_url})")
        else:
            st.sidebar.success("Google Sheets connected")
    else:
        st.sidebar.info("Data source: local CSV files")

    override_date = st.sidebar.date_input("Override today", value=effective_today)
    st.sidebar.caption(
        f"Known payday anchor: {settings['known_payday']} · every {settings['pay_interval_days']} days"
    )
    st.sidebar.caption(f"Paycheck amount: {money(safe_float(settings.get('paycheck_amount'), 0.0))}")
    st.sidebar.caption(
        f"Starting available cash seed: {money(safe_float(settings.get('starting_available_cash'), 0.0))}"
    )

    st.sidebar.divider()
    st.sidebar.subheader("Liabilities")
    if liabilities.empty:
        st.sidebar.info("No liabilities loaded.")
        return override_date

    for index, liability in liabilities.iterrows():
        with st.sidebar.expander(str(liability["name"]), expanded=index == 0):
            comparison = compare_payment_scenarios(
                balance=liability["balance"],
                apr=liability["apr"],
                current_payment=liability["current_payment"],
                test_payment=st.number_input(
                    f"Test higher payment for {liability['name']}",
                    min_value=float(liability["current_payment"]),
                    value=float(liability["current_payment"]),
                    step=10.0,
                    key=f"test_payment_{index}",
                ),
                start_date=override_date,
                due_day=int(liability["due_day"]),
            )
            current = comparison["current"]
            test = comparison["test"]
            st.write(f"Balance: **{money(float(liability['balance']))}**")
            st.write(f"Current payment: **{money(float(liability['current_payment']))}**")
            if current["months_remaining"] is not None and current["payoff_date"] is not None:
                st.write(f"Estimated months remaining: **{current['months_remaining']}**")
                st.write(f"Projected payoff date: **{current['payoff_date'].isoformat()}**")
            else:
                st.warning("Current payment is too low to estimate payoff cleanly.")

            if test["months_remaining"] is not None:
                st.write(f"Months remaining with test payment: **{test['months_remaining']}**")
            else:
                st.write("Months remaining with test payment: **Not available**")
    return override_date


def main() -> None:
    inject_theme()
    ensure_data_files(DATA_DIR)

    settings = load_settings(DATA_DIR)
    transactions = load_transactions(DATA_DIR)
    recurring_bills = load_recurring_bills(DATA_DIR)
    liabilities = load_liabilities(DATA_DIR)

    effective_today = render_sidebar(settings, date.today(), liabilities)

    auto_add_due_paychecks(DATA_DIR, transactions, settings, effective_today)
    transactions = load_transactions(DATA_DIR)

    snapshot = build_budget_snapshot(transactions, recurring_bills, settings, effective_today)
    category_options = get_category_options(transactions, recurring_bills, liabilities)

    render_header(snapshot)
    render_metrics(snapshot)

    render_budget_pies(snapshot)

    render_transaction_forms(category_options, effective_today)
    render_forecast(snapshot)
    render_bills(snapshot)
    render_recent_transactions(transactions)


if __name__ == "__main__":
    main()