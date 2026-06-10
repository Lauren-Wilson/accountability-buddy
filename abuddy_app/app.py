from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.budgeting import (
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
                padding-top: 1.2rem;
                padding-bottom: 4rem;
                max-width: 760px;
            }
            .abuddy-hero {
                position: fixed;
                top: 0.85rem;
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
                    width: 68px;
                    height: 68px;
                    top: 0.5rem;
                    right: 0.45rem;
                }
                .block-container {
                    padding-top: 0.8rem;
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
                <path d="M50 86 L44 104" stroke="#2b1d12" stroke-width="4" stroke-linecap="round" />
                <path d="M70 86 L76 104" stroke="#2b1d12" stroke-width="4" stroke-linecap="round" />
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
    st.markdown('<div class="abuddy-chip">Manual cash-first budgeting</div>', unsafe_allow_html=True)
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
        f"{snapshot['pay_period_start'].isoformat()} to {snapshot['pay_period_end'].isoformat()}: available cash, unpaid recurring bills due before the next payday, and non-recurring spend so far."
    )
    st.plotly_chart(
        pie_figure(build_pay_period_pie_data(snapshot), ["#ffc72c", "#d72638", "#2f9e44"]),
        use_container_width=True,
    )


def render_transaction_forms(category_options: list[str], effective_today: date) -> None:
    st.subheader("Quick cash moves")
    add_col, spend_col = st.columns(2)

    with add_col:
        st.markdown("### + Add Money")
        with st.form("add_money_form", clear_on_submit=True):
            entry_date = st.date_input("Date", value=effective_today, key="add_date")
            amount = st.number_input("Amount", min_value=0.01, step=5.0, value=25.0, key="add_amount")
            category = st.selectbox("Category", category_options, key="add_category")
            note = st.text_input("Note", value="", key="add_note")
            transaction_type = st.selectbox(
                "Transaction type",
                ["income", "adjustment"],
                index=0,
                key="add_transaction_type",
            )
            submitted = st.form_submit_button("Save money entry")
            if submitted:
                append_transaction(
                    DATA_DIR,
                    {
                        "date": entry_date.isoformat(),
                        "amount": amount,
                        "category": category,
                        "note": note,
                        "transaction_type": transaction_type,
                    },
                )
                st.success("Money entry saved.")
                st.rerun()

    with spend_col:
        st.markdown("### - Spend Money")
        with st.form("spend_money_form", clear_on_submit=True):
            entry_date = st.date_input("Date ", value=effective_today, key="spend_date")
            amount = st.number_input("Amount ", min_value=0.01, step=5.0, value=20.0, key="spend_amount")
            category = st.selectbox("Category ", category_options, key="spend_category")
            note = st.text_input("Note ", value="", key="spend_note")
            transaction_type = st.selectbox(
                "Transaction type ",
                ["spend", "debt_payment", "adjustment"],
                index=0,
                key="spend_transaction_type",
            )
            submitted = st.form_submit_button("Save spend entry")
            if submitted:
                signed_amount = -amount if transaction_type == "adjustment" else amount
                append_transaction(
                    DATA_DIR,
                    {
                        "date": entry_date.isoformat(),
                        "amount": signed_amount,
                        "category": category,
                        "note": note,
                        "transaction_type": transaction_type,
                    },
                )
                st.success("Spend entry saved.")
                st.rerun()


def render_forecast(snapshot: dict, category_options: list[str]) -> None:
    st.subheader("What if I spend…")
    with st.form("what_if_form"):
        col1, col2 = st.columns([1, 1])
        hypothetical_amount = col1.number_input("Hypothetical spend", min_value=0.0, step=5.0, value=0.0)
        col2.selectbox("Category", category_options, key="what_if_category")
        submitted = st.form_submit_button("Run forecast")

    if submitted:
        result = evaluate_what_if(snapshot, hypothetical_amount)
        st.markdown('<div class="abuddy-card">', unsafe_allow_html=True)
        st.write(f"Remaining balance after purchase: **{money(result['remaining_after_purchase'])}**")
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
    snapshot = build_budget_snapshot(transactions, recurring_bills, settings, effective_today)
    category_options = get_category_options(transactions, recurring_bills, liabilities)

    render_header(snapshot)
    render_metrics(snapshot)

    render_budget_pies(snapshot)

    render_transaction_forms(category_options, effective_today)
    render_forecast(snapshot, category_options)
    render_bills(snapshot)
    render_recent_transactions(transactions)


if __name__ == "__main__":
    main()
