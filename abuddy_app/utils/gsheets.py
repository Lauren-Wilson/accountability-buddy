"""Google Sheets data backend for Accountability Buddy.

Authentication uses a GCP service account stored in Streamlit secrets
under the ``[gcp_service_account]`` table, plus a top-level
``spreadsheet_id`` key.  See ``.streamlit/secrets.toml.example`` for the
expected shape.

When secrets are not present the helpers return ``False`` / empty DataFrames
so that the local-CSV fallback in ``budgeting.py`` is used automatically.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Maps the internal data-key names used by budgeting.py to worksheet tab titles.
SHEET_TAB_NAMES: dict[str, str] = {
    "transactions": "transactions",
    "recurring_bills": "recurring_bills",
    "liabilities": "liabilities",
    "settings": "settings",
    "balance_snapshots": "balance_snapshots",
    "bill_payment_log": "bill_payment_log",
    "liability_payment_groups": "liability_payment_groups",
    "liability_payment_log": "liability_payment_log",
}

CACHE_TTL_SECONDS = 20


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    """Return *True* when Google Sheets credentials are present in secrets."""
    try:
        return (
            "gcp_service_account" in st.secrets
            and "spreadsheet_id" in st.secrets
        )
    except Exception:
        return False


@st.cache_resource
def _get_client():  # type: ignore[return]
    """Return an authorised gspread client (cached for the process lifetime)."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


@st.cache_resource
def get_spreadsheet():  # type: ignore[return]
    """Return the gspread Spreadsheet object identified by ``spreadsheet_id``."""
    import gspread

    client = _get_client()
    return client.open_by_key(str(st.secrets["spreadsheet_id"]))


def invalidate_sheet_cache() -> None:
    """Clear cached worksheet reads.

    Streamlit cache_data only supports function-wide clear, so this currently
    clears all sheet-tab read cache entries.
    """
    _read_sheet_cached.clear()


def spreadsheet_url() -> str:
    """Return the browser URL for the configured spreadsheet."""
    sid = str(st.secrets.get("spreadsheet_id", ""))
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else ""


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


def ensure_sheet_tabs(
    expected_columns: dict[str, list[str]],
    sample_rows: dict[str, list[dict[str, Any]]],
) -> None:
    """Create missing worksheet tabs and seed them with headers + sample data.

    Existing tabs are left untouched.  Missing columns are *not* added to
    existing sheets—column names are assumed stable once a sheet is set up.
    """
    import gspread

    if st.session_state.get("abuddy_sheet_bootstrapped"):
        return

    spreadsheet = get_spreadsheet()
    existing_tabs = {ws.title for ws in spreadsheet.worksheets()}

    for key, columns in expected_columns.items():
        tab_name = SHEET_TAB_NAMES[key]
        if tab_name not in existing_tabs:
            worksheet = spreadsheet.add_worksheet(
                title=tab_name,
                rows=1000,
                cols=max(len(columns), 10),
            )
            worksheet.append_row(columns, value_input_option="USER_ENTERED")
            for row in sample_rows.get(key, []):
                worksheet.append_row(
                    [str(row.get(col, "")) for col in columns],
                    value_input_option="USER_ENTERED",
                )
        else:
            # Ensure the tab has a header row if it was created manually and
            # is completely empty.
            worksheet = spreadsheet.worksheet(tab_name)
            try:
                all_values = worksheet.get_all_values()
            except gspread.exceptions.APIError:
                continue

            has_any_content = any(any(str(cell).strip() for cell in row) for row in all_values)
            header = all_values[0] if all_values else []

            if not has_any_content or not header:
                worksheet.append_row(columns, value_input_option="USER_ENTERED")
                for row in sample_rows.get(key, []):
                    worksheet.append_row(
                        [str(row.get(col, "")) for col in columns],
                        value_input_option="USER_ENTERED",
                    )

    invalidate_sheet_cache()
    st.session_state["abuddy_sheet_bootstrapped"] = True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _read_sheet_cached(
    spreadsheet_id: str,
    key: str,
    expected_columns: tuple[str, ...],
) -> pd.DataFrame:
    import gspread

    try:
        spreadsheet = get_spreadsheet()
        worksheet = spreadsheet.worksheet(SHEET_TAB_NAMES[key])
        records = worksheet.get_all_records(default_blank="")
        if not records:
            return pd.DataFrame(columns=list(expected_columns))
        frame = pd.DataFrame(records)
        for col in expected_columns:
            if col not in frame.columns:
                frame[col] = ""
        return frame[list(expected_columns)].copy()
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame(columns=list(expected_columns))
    except Exception as exc:
        st.warning(f"Could not read '{key}' from Google Sheets: {exc}")
        return pd.DataFrame(columns=list(expected_columns))


def read_sheet(key: str, expected_columns: list[str]) -> pd.DataFrame:
    """Read a worksheet tab and return a normalised DataFrame.

    Returns an empty DataFrame (with the expected columns) on any error so
    the rest of the app degrades gracefully.
    """
    sid = str(st.secrets.get("spreadsheet_id", ""))
    return _read_sheet_cached(
        spreadsheet_id=sid,
        key=key,
        expected_columns=tuple(expected_columns),
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def append_row(key: str, row: dict[str, Any], expected_columns: list[str]) -> None:
    """Append a single row to the named worksheet tab."""
    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet(SHEET_TAB_NAMES[key])
    values = [str(row.get(col, "")) for col in expected_columns]
    worksheet.append_row(values, value_input_option="USER_ENTERED")
    invalidate_sheet_cache()


def write_sheet(key: str, frame: pd.DataFrame, expected_columns: list[str]) -> None:
    """Overwrite a worksheet tab with headers and all rows from ``frame``."""
    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet(SHEET_TAB_NAMES[key])

    normalized = frame.copy()
    for col in expected_columns:
        if col not in normalized.columns:
            normalized[col] = ""
    normalized = normalized[expected_columns]

    worksheet.clear()
    worksheet.append_row(expected_columns, value_input_option="USER_ENTERED")
    if not normalized.empty:
        rows = normalized.fillna("").astype(str).values.tolist()
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    invalidate_sheet_cache()
