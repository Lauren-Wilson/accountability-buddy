from __future__ import annotations

from datetime import date
from pathlib import Path
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.budgeting import (
    append_balance_snapshot,
    append_bill_payment_log,
    auto_add_due_paychecks,
    append_transaction,
    build_pay_period_pie_data,
    build_budget_snapshot,
    build_recurring_status_pie_data,
    append_liability_payment_log_rows,
    ensure_data_files,
    evaluate_what_if,
    get_category_options,
    has_group_payment_for_month,
    load_balance_snapshots,
    load_bill_payment_log,
    load_liability_payment_groups,
    load_liability_payment_log,
    load_liabilities,
    load_recurring_bills,
    load_settings,
    load_transactions,
    save_liabilities,
)
from utils.debt_math import apply_group_payment, estimate_group_payoff
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
                --abuddy-text: #2b1d12;
            }
            html, body, [data-testid="stAppViewContainer"] {
                color-scheme: light !important;
            }
            .stApp {
                background: linear-gradient(180deg, var(--abuddy-cream) 0%, #fffaf1 100%);
                color: var(--abuddy-text);
            }
            .block-container {
                padding-top: 3.5rem;
                padding-bottom: 4rem;
                max-width: 760px;
            }
            [data-testid="stMetricLabel"],
            [data-testid="stMetricValue"],
            [data-testid="stMetricDelta"] {
                color: var(--abuddy-text) !important;
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
                border: 1px solid rgba(215, 38, 56, 0.22) !important;
                min-height: 3.3rem;
                font-weight: 800;
                font-size: 1.05rem;
                background: #fff3d6 !important;
                color: #2b1d12 !important;
                -webkit-appearance: none;
                appearance: none;
                box-shadow: 0 10px 22px rgba(92, 43, 20, 0.10);
                transition: transform 160ms ease, background-color 160ms ease, box-shadow 160ms ease;
            }
            .stFormSubmitButton > button {
                width: 100%;
                min-height: 3.1rem;
                border-radius: 999px;
                font-weight: 800;
                border: 1px solid rgba(215, 38, 56, 0.22) !important;
                background: #fff3d6 !important;
                color: #2b1d12 !important;
                -webkit-appearance: none;
                appearance: none;
                box-shadow: 0 10px 22px rgba(92, 43, 20, 0.10);
                transition: transform 160ms ease, background-color 160ms ease, box-shadow 160ms ease;
            }
            .stButton > button:hover,
            .stFormSubmitButton > button:hover {
                background: #ffe8a8 !important;
                color: #2b1d12 !important;
                transform: translateY(-1px);
                box-shadow: 0 14px 26px rgba(92, 43, 20, 0.14);
            }
            .stButton > button:active,
            .stFormSubmitButton > button:active {
                background: #ffd978 !important;
                color: #2b1d12 !important;
                transform: translateY(0);
                box-shadow: 0 8px 16px rgba(92, 43, 20, 0.12);
            }
            .stButton > button:focus,
            .stFormSubmitButton > button:focus {
                outline: 2px solid rgba(215, 38, 56, 0.32) !important;
                outline-offset: 2px;
            }
            .stTextInput input,
            .stNumberInput input,
            [data-baseweb="select"] > div {
                border-radius: 999px !important;
                color: var(--abuddy-text) !important;
                background: rgba(255, 255, 255, 0.96) !important;
                font-size: 1.15rem !important;
                min-height: 3.2rem !important;
                border: 1px solid rgba(215, 38, 56, 0.18) !important;
            }
            [data-testid="stWidgetLabel"] p,
            .stCaption,
            p {
                color: var(--abuddy-text) !important;
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
                    padding-left: 0.7rem;
                    padding-right: 0.7rem;
                }
                [data-testid="stWidgetLabel"] p {
                    font-size: 1.05rem !important;
                    font-weight: 700 !important;
                }
                .stTextInput input,
                .stNumberInput input,
                [data-baseweb="select"] > div {
                    font-size: 1.25rem !important;
                    min-height: 3.6rem !important;
                }
                .stButton > button {
                    min-height: 3.8rem;
                    font-size: 1.15rem;
                }
                .stFormSubmitButton > button {
                    min-height: 3.8rem;
                    font-size: 1.15rem;
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


def parse_balance_input(raw_value: str) -> float | None:
    value = str(raw_value or "").strip()
    if not value:
        return None

    normalized = re.sub(r"[,$\s]", "", value)
    if not normalized:
        return None

    try:
        return float(normalized)
    except ValueError:
        return None


def _queue_decimal_for_field(field_key: str) -> None:
    st.session_state[f"{field_key}__pending_decimal"] = True


def _apply_cents_precision(raw_value: str) -> str:
    digits_only = re.sub(r"\D", "", str(raw_value or ""))
    if not digits_only:
        return str(raw_value or "")

    padded = digits_only.zfill(3)
    whole_part = str(int(padded[:-2]))
    fractional_part = padded[-2:]
    return f"{whole_part}.{fractional_part}"


def _apply_pending_decimal(field_key: str) -> None:
    pending_key = f"{field_key}__pending_decimal"
    if not st.session_state.get(pending_key):
        return

    current = str(st.session_state.get(field_key, ""))
    st.session_state[field_key] = _apply_cents_precision(current)
    st.session_state[pending_key] = False


def pie_figure(pie_data: pd.DataFrame, colors: list[str]) -> go.Figure:
    pulls = pie_data["pull"].tolist() if "pull" in pie_data.columns else None
    figure = go.Figure(
        data=[
            go.Pie(
                labels=pie_data["label"],
                values=pie_data["value"],
                hole=0.0,
                sort=False,
                marker={"colors": colors},
                pull=pulls,
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
    st.markdown('<div class="abuddy-title">Accountability Buddy</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="abuddy-subtitle">A-Buddy helps you decide what you can safely spend on {snapshot["reference_date"].isoformat()}.</div>',
        unsafe_allow_html=True,
    )


def render_metrics(snapshot: dict) -> None:
    col1, col2 = st.columns(2)
    col3, col4 = st.columns(2)
    balance_label = "Current available balance"
    if snapshot.get("has_manual_balance_snapshot"):
        balance_label = "Current balance (manual)"
    col1.metric(balance_label, money(snapshot["current_available_balance"]))
    col2.metric("Next payday", snapshot["next_payday"].strftime("%a, %b %d"))
    col3.metric("Days until payday", str(snapshot["days_until_payday"]))
    col4.metric("Projected end-of-month", money(snapshot["projected_end_of_month_balance"]))
    if snapshot.get("last_balance_update_date") is not None:
        st.caption(f"Last balance update date: {snapshot['last_balance_update_date'].isoformat()}")
    else:
        st.caption("No manual balance snapshot yet. Current balance is derived from transactions.")


def render_budget_pies(snapshot: dict) -> None:
    st.subheader("Recurring bills this month")
    st.caption("Calendar-month status: bills are marked paid when a matching transaction exists this month.")
    st.plotly_chart(
        pie_figure(build_recurring_status_pie_data(snapshot), ["#2f9e44", "#d72638"]),
        use_container_width=True,
    )

    st.subheader("Monthly cash and bill status")
    st.caption(
        "Paid/unpaid recurring bills, manual spending, remaining after unpaid bills, and leftover from prior month."
    )
    st.plotly_chart(
        pie_figure(build_pay_period_pie_data(snapshot), ["#2f9e44", "#d72638", "#3b82f6", "#ffc72c", "#8b5e3c"]),
        use_container_width=True,
    )


def render_balance_update(snapshot: dict, effective_today: date) -> None:
    st.subheader("Update Current Balance")
    with st.form("balance_snapshot_form", clear_on_submit=True):
        snapshot_date = st.date_input("Balance date", value=effective_today, key="balance_snapshot_date")
        balance_input = st.text_input("Current balance", value="", key="balance_snapshot_amount", placeholder="0.00")
        note = st.text_input("Note (optional)", value="", key="balance_snapshot_note")
        submitted = st.form_submit_button("Save balance snapshot", use_container_width=True)

    parsed_balance = parse_balance_input(balance_input)
    if balance_input.strip() and parsed_balance is not None:
        st.caption(f"Balance preview: **{money(parsed_balance)}**")

    if submitted:
        if parsed_balance is None:
            st.error("Enter a valid current balance.")
            return

        append_balance_snapshot(
            DATA_DIR,
            {
                "date": snapshot_date.isoformat(),
                "balance": parsed_balance,
                "note": note,
            },
        )
        st.success("Current balance updated.")
        st.rerun()


def render_transaction_forms(category_options: list[str], effective_today: date) -> None:
    st.subheader("Quick cash moves")

    entry_date = st.date_input("Date", value=effective_today, key="quick_date")
    _apply_pending_decimal("quick_amount_text")
    amount_input = st.text_input(
        "Amount",
        value="",
        key="quick_amount_text",
        placeholder="0.00",
    )
    st.button(
        "Add decimal (.)",
        key="quick_amount_decimal",
        use_container_width=True,
        on_click=_queue_decimal_for_field,
        args=("quick_amount_text",),
    )

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
        st.success("Transaction saved.")
        st.rerun()


def render_forecast(snapshot: dict) -> None:
    st.subheader("What if I spend…")
    _apply_pending_decimal("what_if_amount_text")
    amount_input = st.text_input(
        "Spend amount now",
        value="",
        key="what_if_amount_text",
        placeholder="0.00",
    )
    st.button(
        "Add decimal (.)",
        key="what_if_amount_decimal",
        use_container_width=True,
        on_click=_queue_decimal_for_field,
        args=("what_if_amount_text",),
    )
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
        after_remaining_bills_before_future_income = result.get("after_remaining_bills_before_future_income", 0.0)
        st.markdown('<div class="abuddy-card">', unsafe_allow_html=True)
        st.write(f"Current manual balance: **{money(snapshot['current_available_balance'])}**")
        st.write(f"Unpaid recurring bills remaining: **{money(result['unpaid_bills_remaining'])}**")
        st.write(f"Hypothetical spend: **{money(result['hypothetical_spend'])}**")
        st.write(
            f"Projected balance after unpaid bills and hypothetical spend: **{money(after_remaining_bills_before_future_income)}**"
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


def render_bills(snapshot: dict, effective_today: date) -> None:
    st.subheader("Recurring bills this month")
    if snapshot["bills_this_month"].empty:
        st.info("No active recurring bills found.")
        return

    bills = snapshot["bills_this_month"].copy()
    display = bills[["bill_name", "amount", "due_date", "category", "is_paid"]].copy()
    display["amount"] = display["amount"].map(money)
    display["due_date"] = display["due_date"].astype(str)
    display["status"] = display["is_paid"].map(lambda paid: "Paid" if bool(paid) else "Unpaid")
    display = display.drop(columns=["is_paid"])

    def _status_style(value: object) -> str:
        if str(value) == "Paid":
            return "color: #1b8f5a; font-weight: 800;"
        return "color: #d72638; font-weight: 800;"

    styled_display = display.style.applymap(_status_style, subset=["status"])
    st.dataframe(styled_display, use_container_width=True, hide_index=True)

    unpaid = bills[~bills["is_paid"]]
    if not unpaid.empty:
        st.caption("Mark monthly recurring bills as paid:")
        for _, bill in unpaid.iterrows():
            label = f"Mark paid: {bill['bill_name']} ({money(float(bill['amount']))})"
            if st.button(label, key=f"mark_paid_{snapshot['month_key']}_{bill['bill_id']}", use_container_width=True):
                created = append_bill_payment_log(
                    DATA_DIR,
                    {
                        "bill_id": bill["bill_id"],
                        "paid_date": effective_today.isoformat(),
                        "month": snapshot["month_key"],
                        "amount_paid": float(bill["amount"]),
                        "note": "",
                    },
                )
                if created:
                    st.success(f"Logged {bill['bill_name']} as paid for {snapshot['month_key']}.")
                    st.rerun()
                st.info(f"{bill['bill_name']} is already marked paid for {snapshot['month_key']}.")

    projected_after_unpaid = snapshot["current_available_balance"] - snapshot["remaining_recurring_bills_total"]
    st.caption(
        f"Paid recurring bills this month: {money(snapshot['paid_recurring_bills_total'])} · Unpaid recurring bills this month: {money(snapshot['remaining_recurring_bills_total'])} · Projected balance after unpaid bills: {money(projected_after_unpaid)}"
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


def _ensure_groups_cover_liabilities(
    liability_payment_groups: pd.DataFrame,
    liabilities: pd.DataFrame,
) -> pd.DataFrame:
    groups = liability_payment_groups.copy()
    if groups.empty:
        groups = pd.DataFrame(columns=["group_id", "group_name", "payment_amount", "strategy", "active"])

    for column in ["group_id", "group_name", "payment_amount", "strategy"]:
        if column not in groups.columns:
            groups[column] = "" if column in {"group_id", "group_name", "strategy"} else 0.0
    if "active" not in groups.columns:
        groups["active"] = True

    groups["group_id"] = groups["group_id"].astype(str).str.strip().str.lower()
    groups["group_name"] = groups["group_name"].astype(str).str.strip()
    groups["payment_amount"] = pd.to_numeric(groups["payment_amount"], errors="coerce").fillna(0.0)
    groups["active"] = groups["active"].astype(str).str.strip().str.lower().isin(["true", "1", "yes", "y"]) | (groups["active"] == True)
    groups["strategy"] = "priority"

    if liabilities.empty:
        return groups[["group_id", "group_name", "payment_amount", "strategy", "active"]]

    liab = liabilities.copy()
    liab["group_id"] = liab["group_id"].fillna("").astype(str).str.strip().str.lower()
    if "min_payment" not in liab.columns:
        liab["min_payment"] = 0.0
    liab["min_payment"] = pd.to_numeric(liab["min_payment"], errors="coerce").fillna(0.0)
    liab = liab[liab["group_id"] != ""]

    existing_group_ids = set(groups["group_id"].tolist())
    inferred_rows = []
    for group_id, frame in liab.groupby("group_id"):
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
        groups = pd.concat([groups, pd.DataFrame(inferred_rows)], ignore_index=True)

    groups.loc[groups["group_name"] == "", "group_name"] = groups["group_id"]
    groups = groups.drop_duplicates(subset=["group_id"], keep="last")
    return groups[["group_id", "group_name", "payment_amount", "strategy", "active"]]


def render_sidebar(
    settings: dict,
    effective_today: date,
    liabilities: pd.DataFrame,
    liability_payment_groups: pd.DataFrame,
    liability_payment_log: pd.DataFrame,
) -> date:
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
    st.sidebar.subheader("Liability Payments")
    if liabilities.empty or liability_payment_groups.empty:
        st.sidebar.info("No liability groups configured.")
        return override_date

    if "last_liability_payment_table" in st.session_state:
        st.sidebar.caption("Most recent applied payment")
        st.sidebar.dataframe(st.session_state["last_liability_payment_table"], use_container_width=True, hide_index=True)

    for _, group in liability_payment_groups[liability_payment_groups["active"]].iterrows():
        group_id = str(group["group_id"])
        group_name = str(group["group_name"])
        payment_amount = float(group["payment_amount"])
        strategy = str(group["strategy"])

        group_liabilities = liabilities[
            (liabilities["group_id"] == group_id)
            & (liabilities["active"])
            & (liabilities["balance"] > 0)
        ].copy()

        with st.sidebar.expander(f"{group_name} ({group_id})", expanded=False):
            total_balance = float(group_liabilities["balance"].sum()) if not group_liabilities.empty else 0.0
            estimate = estimate_group_payoff(group_liabilities, payment_amount, strategy, override_date)
            st.write(f"Payment amount: **{money(payment_amount)}**")
            st.write(f"Active liabilities: **{len(group_liabilities)}**")
            st.write(f"Total balance: **{money(total_balance)}**")

            if estimate["months_remaining"] is not None and estimate["projected_payoff_date"] is not None:
                st.write(f"Estimated months remaining: **{estimate['months_remaining']}**")
                st.write(f"Projected payoff month: **{estimate['projected_payoff_date'].strftime('%Y-%m')}**")
            else:
                st.warning("Could not estimate payoff with the current payment amount.")

            test_payment = st.number_input(
                "What if I paid this amount instead?",
                min_value=0.0,
                value=payment_amount,
                step=25.0,
                key=f"what_if_group_{group_id}",
            )
            test_estimate = estimate_group_payoff(group_liabilities, test_payment, strategy, override_date)
            if test_estimate["months_remaining"] is not None:
                st.caption(
                    f"Forecast only: {test_estimate['months_remaining']} months remaining, payoff around {test_estimate['projected_payoff_date'].strftime('%Y-%m')}"
                )
            else:
                st.caption("Forecast only: payoff cannot be estimated at this payment level.")

            payment_month = override_date.strftime("%Y-%m")
            already_paid_this_month = has_group_payment_for_month(liability_payment_log, group_id, payment_month)
            if already_paid_this_month:
                st.warning("A payment has already been applied for this group this month.")

            allow_duplicate = st.checkbox(
                "Apply another payment anyway.",
                value=False,
                key=f"allow_duplicate_{group_id}_{payment_month}",
                disabled=not already_paid_this_month,
            )

            apply_clicked = st.button(
                "Apply Monthly Payment",
                key=f"apply_monthly_group_{group_id}",
                use_container_width=True,
            )

            if apply_clicked:
                if group_liabilities.empty:
                    st.info("All liabilities in this group are already paid off.")
                    continue

                if already_paid_this_month and not allow_duplicate:
                    st.warning("A payment has already been applied for this group this month.")
                    continue

                updated_liabilities, payment_rows, unapplied_remainder = apply_group_payment(
                    group_liabilities,
                    payment_amount,
                    strategy,
                )

                liabilities_after = liabilities.copy()
                updates = updated_liabilities[["liability_id", "balance", "active"]].copy()
                updates = updates.set_index("liability_id")
                for liability_id, row in updates.iterrows():
                    mask = liabilities_after["liability_id"] == liability_id
                    liabilities_after.loc[mask, "balance"] = float(row["balance"])
                    liabilities_after.loc[mask, "active"] = bool(row["active"])

                save_liabilities(DATA_DIR, liabilities_after)

                payment_log_rows = []
                for _, payment_row in payment_rows.iterrows():
                    payment_log_rows.append(
                        {
                            "payment_date": override_date.isoformat(),
                            "payment_month": payment_month,
                            "group_id": group_id,
                            "liability_id": payment_row["liability_id"],
                            "amount_applied": float(payment_row["amount_applied"]),
                            "interest_applied": float(payment_row["interest_applied"]),
                            "principal_applied": float(payment_row["principal_applied"]),
                            "starting_balance": float(payment_row["starting_balance"]),
                            "ending_balance": float(payment_row["ending_balance"]),
                            "note": "Applied from sidebar monthly group payment",
                        }
                    )

                append_liability_payment_log_rows(DATA_DIR, payment_log_rows)

                confirmation = payment_rows.copy()
                if confirmation.empty:
                    confirmation = pd.DataFrame(
                        [{
                            "liability_id": "(none)",
                            "amount_applied": 0.0,
                            "interest_applied": 0.0,
                            "principal_applied": 0.0,
                            "starting_balance": 0.0,
                            "ending_balance": 0.0,
                        }]
                    )
                confirmation["amount_applied"] = confirmation["amount_applied"].map(money)
                confirmation["interest_applied"] = confirmation["interest_applied"].map(money)
                confirmation["principal_applied"] = confirmation["principal_applied"].map(money)
                confirmation["starting_balance"] = confirmation["starting_balance"].map(money)
                confirmation["ending_balance"] = confirmation["ending_balance"].map(money)
                st.session_state["last_liability_payment_table"] = confirmation

                if unapplied_remainder > 0:
                    st.info(f"{money(unapplied_remainder)} remained after paying off all active liabilities in this group.")
                st.success("Monthly payment applied and liabilities updated.")
                st.rerun()

    return override_date


def main() -> None:
    inject_theme()
    ensure_data_files(DATA_DIR)

    settings = load_settings(DATA_DIR)
    transactions = load_transactions(DATA_DIR)
    recurring_bills = load_recurring_bills(DATA_DIR)
    liabilities = load_liabilities(DATA_DIR)
    liability_payment_groups = load_liability_payment_groups(DATA_DIR)
    liability_payment_groups = _ensure_groups_cover_liabilities(liability_payment_groups, liabilities)
    liability_payment_log = load_liability_payment_log(DATA_DIR)
    balance_snapshots = load_balance_snapshots(DATA_DIR)
    bill_payment_log = load_bill_payment_log(DATA_DIR)

    effective_today = render_sidebar(
        settings,
        date.today(),
        liabilities,
        liability_payment_groups,
        liability_payment_log,
    )

    auto_add_due_paychecks(DATA_DIR, transactions, settings, effective_today)
    transactions = load_transactions(DATA_DIR)

    snapshot = build_budget_snapshot(
        transactions,
        recurring_bills,
        settings,
        effective_today,
        balance_snapshots=balance_snapshots,
        bill_payment_log=bill_payment_log,
    )
    category_options = get_category_options(transactions, recurring_bills, liabilities)

    render_header(snapshot)
    render_metrics(snapshot)

    render_budget_pies(snapshot)

    render_transaction_forms(category_options, effective_today)
    render_balance_update(snapshot, effective_today)
    render_forecast(snapshot)
    render_bills(snapshot, effective_today)
    render_recent_transactions(transactions)


if __name__ == "__main__":
    main()