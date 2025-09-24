# NAP.py
import streamlit as st
import pandas as pd
import uuid
import requests
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

# -----------------------
# Config / secrets
# -----------------------
st.set_page_config("NAP Logger", layout="wide")
SHEET_ID = st.secrets["SHEET_ID"]
SHEET_TAB = st.secrets.get("SHEET_TAB_NAME", "Violations")
SERVICE_ACCOUNT_FILE = st.secrets.get("SERVICE_ACCOUNT_FILE", "service_account.json")
ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]

# ----------------------------------------------------------------
# Apps Script endpoint config (replace if different)
# ----------------------------------------------------------------
APP_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzjI4dIOKsINA5trETvaDcLfof9tIpshmwTTvVnTIUjQmE4cD1HPa_dhZnRcp6vW1BZ/exec"
APP_SCRIPT_PASSWORD = "supersecret123"   # <-- must match PASSWORD in your Apps Script

# -----------------------
# Authenticate to Google Sheets (still kept for read fallback)
# -----------------------
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
sheets_service = build("sheets", "v4", credentials=creds)

# -----------------------
# Helpers
# -----------------------
# robust sheet helpers
def read_sheet_as_df():
    """
    Read the sheet and normalize rows so each row has equal length.
    Returns a pandas.DataFrame.
    """
    range_name = f"{SHEET_TAB}!A1:Z10000"
    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=range_name
    ).execute()
    vals = resp.get("values", [])
    if not vals:
        return pd.DataFrame()

    # header is first row
    headers = vals[0]
    data_rows = vals[1:]

    # find max length across header and all rows
    max_len = max(len(headers), max((len(r) for r in data_rows), default=0))

    # pad header to max_len (if someone added trailing columns in data)
    headers = headers + ["col_{}".format(i) for i in range(len(headers)+1, max_len+1)]

    # normalize each data row to max_len by padding with empty strings
    normalized_rows = []
    for r in data_rows:
        if len(r) < max_len:
            normalized_rows.append(r + [""] * (max_len - len(r)))
        elif len(r) > max_len:
            # If a row is longer than max_len (rare), truncate to max_len
            normalized_rows.append(r[:max_len])
        else:
            normalized_rows.append(r)

    # build DataFrame
    df = pd.DataFrame(normalized_rows, columns=headers)
    return df

# -----------------------
# New: send add request to Apps Script
# -----------------------
def append_row(row_list):
    """
    row_list is expected: [uid, timestamp, reporter, violator, category, description, coords, proofs]
    This function will POST to the Apps Script web app to append the row.
    """
    # map to named fields expected by Apps Script
    payload = {
        "password": APP_SCRIPT_PASSWORD,
        "action": "add",
        "uid": row_list[0] if len(row_list) > 0 else "",
        "timestamp": row_list[1] if len(row_list) > 1 else "",
        "reporter": row_list[2] if len(row_list) > 2 else "",
        "violator": row_list[3] if len(row_list) > 3 else "",
        "category": row_list[4] if len(row_list) > 4 else "",
        "description": row_list[5] if len(row_list) > 5 else "",
        "coords": row_list[6] if len(row_list) > 6 else "",
        "proofs": row_list[7] if len(row_list) > 7 else ""
    }
    try:
        resp = requests.post(APP_SCRIPT_URL, json=payload, timeout=10)
        resp.raise_for_status()
        j = resp.json() if resp.text else {}
        if not j.get("ok", False):
            raise Exception(f"Apps Script returned error: {j}")
    except Exception as e:
        # bubble up so UI can show error
        raise

# -----------------------
# New: update via Apps Script (uses uid)
# -----------------------
def update_row_by_uid(uid, data_dict):
    """
    Update a row by uid via Apps Script.
    data_dict: mapping of column_name -> value (e.g. {"description": "new text", "category":"Other"})
    """
    payload = {"password": APP_SCRIPT_PASSWORD, "action": "update", "uid": uid}
    # copy allowed keys
    for k, v in data_dict.items():
        # only include simple values (strings/numbers)
        if isinstance(v, (str, int, float)) or v is None:
            payload[k] = "" if v is None else str(v)
    try:
        resp = requests.post(APP_SCRIPT_URL, json=payload, timeout=10)
        resp.raise_for_status()
        j = resp.json() if resp.text else {}
        if not j.get("ok", False):
            raise Exception(f"Apps Script returned error: {j}")
    except Exception:
        raise

# Keep a fallback update_by_sheet_index that uses Sheets API (in case uid cannot be found)
def update_row_by_sheet_index_fallback(sheet_row_index, row_values):
    header_range = f"{SHEET_TAB}!A1:1"
    header = sheets_service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=header_range).execute().get("values", [[]])[0]
    if not header:
        st.error("Sheet has no header row.")
        return
    last_col = chr(ord("A") + len(header) - 1)
    target_range = f"{SHEET_TAB}!A{sheet_row_index}:{last_col}{sheet_row_index}"
    body = {"values": [row_values]}
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=target_range,
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

# -----------------------
# Simple auth
# -----------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔒 NAP Logger — login")
    pw = st.text_input("Admin password", type="password")
    if st.button("Log in"):
        if pw == ADMIN_PASSWORD:
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()

# -----------------------
# Main UI
# -----------------------
st.title("📋 NAP Violation Logs (Google Sheets)")

# Load data
df = read_sheet_as_df()
# --- Editable table with multi-line description ---
if not df.empty:
    st.subheader("Existing logs — edit then press Save changes")

    # Insert sheet_row index (sheet rows start at 2 because header is row 1)
    df_display = df.copy()
    df_display.insert(0, "sheet_row", [i + 2 for i in range(len(df_display))])

    # Configure column behavior: make 'description' a multiline text editor and 'proof_link' wide
    column_cfg = {}
    if "description" in df_display.columns:
        column_cfg["description"] = st.column_config.TextColumn(
            "description",
            help="Detailed description of the incident",
            width="wide",      # wide so it shows more text
            max_chars=2000
        )
    if "proof_link" in df_display.columns:
        column_cfg["proof_link"] = st.column_config.TextColumn(
            "proof_link",
            help="Comma-separated proof URLs",
            width="large",
            max_chars=2000
        )

    # Show editable data editor
    edited = st.data_editor(
        df_display,
        num_rows="dynamic",
        use_container_width=True,
        column_config=column_cfg,
        hide_index=True,
        key="nap_logs_editor"
    )

    # Save button: write back any changes to the sheet
    if st.button("Save changes"):
        # We will update every row shown in the editor (safe approach)
        # Get the header order from the original sheet (df)
        header_vals = list(df.columns)

        # Iterate through edited rows and update the corresponding sheet row
        updated_count = 0
        for _, row in edited.iterrows():
            try:
                sheet_row = int(row["sheet_row"])
            except Exception:
                # skip rows without sheet_row
                continue

            # Build values in the same order as header_vals
            row_values = [row.get(col, "") for col in header_vals]

            # Try to obtain uid for update. Prefer edited 'uid' column if present,
            # else use original df to fetch the uid from that sheet_row.
            uid_val = None
            if "uid" in row.index and row.get("uid"):
                uid_val = row.get("uid")
            else:
                # compute index into original df: sheet_row-2
                try:
                    orig_idx = sheet_row - 2
                    if 0 <= orig_idx < len(df):
                        if "uid" in df.columns:
                            uid_val = df.iloc[orig_idx].get("uid", "")
                except Exception:
                    uid_val = None

            # If uid found, send update via Apps Script
            if uid_val:
                # build data dict mapping header->value (only include columns present in header)
                data_dict = {}
                for col in header_vals:
                    # get updated value from edited row (if exists), else ""
                    data_dict[col] = row.get(col, "")
                # attempt update via webapp
                try:
                    update_row_by_uid(uid_val, data_dict)
                    updated_count += 1
                    continue
                except Exception as e:
                    # fallback to direct Sheets API update if the webapp update fails
                    try:
                        update_row_by_sheet_index_fallback(sheet_row, row_values)
                        updated_count += 1
                        continue
                    except Exception as e2:
                        st.error(f"Failed to update sheet row {sheet_row}: {e}; fallback error: {e2}")
                        continue
            else:
                # No uid available — fallback to direct Sheets API update
                try:
                    update_row_by_sheet_index_fallback(sheet_row, row_values)
                    updated_count += 1
                except Exception as e:
                    st.error(f"Failed to update sheet row {sheet_row}: {e}")

        st.success(f"Saved updates to {updated_count} row(s).")
else:
    st.info("No logs found. Make sure your sheet has a header row and data.")
# Add new report form
st.subheader("➕ Add new report")
with st.form("add_form"):
    c1, c2 = st.columns(2)
    with c1:
        reporter = st.text_input("Reporter")
        violator = st.text_input("Violator", value="WCE")
        category = st.selectbox("Category", ["Attack on city", "Attack on resource", "Banner dismantle", "Scouting", "Recruiting", "Other"])
    with c2:
        date_val = st.date_input("Date (UTC)", value=datetime.utcnow().date())
        time_val = st.time_input("Time (UTC)", value=datetime.utcnow().time().replace(microsecond=0))
        coords = st.text_input("Coordinates", placeholder="X:123 Y:456")
    description = st.text_area("Description", height=120)
    proof_links = st.text_input("Proof links (optional, comma separated) — paste image URLs or drive links")
    submitted = st.form_submit_button("Add report")

if submitted:
    if not reporter.strip() or not description.strip():
        st.error("Reporter and description required.")
    else:
        uid = uuid.uuid4().hex[:10]
        timestamp = datetime.combine(date_val, time_val).isoformat()
        links = proof_links.strip()
        row = [uid, timestamp, reporter, violator, category, description, coords, links]
        try:
            append_row(row)
            st.success("Report added to sheet.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to add report: {e}")
