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
}


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


def get_spreadsheet():  # type: ignore[return]
    """Return the gspread Spreadsheet object identified by ``spreadsheet_id``."""
    import gspread

    client = _get_client()
    return client.open_by_key(str(st.secrets["spreadsheet_id"]))


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
                header = worksheet.row_values(1)
            except gspread.exceptions.APIError:
                header = []
            if not header:
                worksheet.append_row(columns, value_input_option="USER_ENTERED")
                for row in sample_rows.get(key, []):
                    worksheet.append_row(
                        [str(row.get(col, "")) for col in columns],
                        value_input_option="USER_ENTERED",
                    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_sheet(key: str, expected_columns: list[str]) -> pd.DataFrame:
    """Read a worksheet tab and return a normalised DataFrame.

    Returns an empty DataFrame (with the expected columns) on any error so
    the rest of the app degrades gracefully.
    """
    import gspread

    try:
        spreadsheet = get_spreadsheet()
        worksheet = spreadsheet.worksheet(SHEET_TAB_NAMES[key])
        records = worksheet.get_all_records(default_blank="")
        if not records:
            return pd.DataFrame(columns=expected_columns)
        frame = pd.DataFrame(records)
        for col in expected_columns:
            if col not in frame.columns:
                frame[col] = ""
        return frame[expected_columns].copy()
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame(columns=expected_columns)
    except Exception as exc:
        st.warning(f"Could not read '{key}' from Google Sheets: {exc}")
        return pd.DataFrame(columns=expected_columns)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def append_row(key: str, row: dict[str, Any], expected_columns: list[str]) -> None:
    """Append a single row to the named worksheet tab."""
    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet(SHEET_TAB_NAMES[key])
    values = [str(row.get(col, "")) for col in expected_columns]
    worksheet.append_row(values, value_input_option="USER_ENTERED")
