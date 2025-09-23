# NAP.py
import streamlit as st
import pandas as pd
import uuid
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

# -----------------------
# Authenticate to Google Sheets
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


def append_row(row_list):
    """Append a row (list) to the sheet. This function will append values as-is."""
    body = {"values": [row_list]}
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A1",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()


def update_row_by_sheet_index(sheet_row_index, row_values):
    """
    Update a specific row in the sheet.
    sheet_row_index: actual sheet row number (e.g. 2 for first data row)
    row_values: list of values matching the header columns (or shorter)
    This function will fetch the current header, compute the last column, and
    pad/truncate row_values to match header length before writing.
    """
    # get header row to determine number of columns
    header_range = f"{SHEET_TAB}!A1:1"
    header_resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=header_range
    ).execute()
    headers = header_resp.get("values", [[]])[0]
    if not headers:
        st.error("Sheet header missing.")
        return

    ncols = len(headers)
    # pad or truncate row_values to ncols
    if len(row_values) < ncols:
        row_values = row_values + [""] * (ncols - len(row_values))
    elif len(row_values) > ncols:
        row_values = row_values[:ncols]

    # compute Excel-like last column (A..Z, then AA..), we can compute range by index
    # simpler approach: build range using column index to letter mapping
    def col_letter_from_index(idx):
        """1-indexed -> column letter; e.g. 1 -> 'A', 27 -> 'AA'"""
        letters = ""
        while idx > 0:
            idx, rem = divmod(idx - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    last_col_letter = col_letter_from_index(ncols)
    target_range = f"{SHEET_TAB}!A{sheet_row_index}:{last_col_letter}{sheet_row_index}"

    body = {"values": [row_values]}
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=target_range,
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

def append_row(row_list):
    body = {"values": [row_list]}
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A1",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

def update_row_by_sheet_index(sheet_row_index, row_values):
    # sheet_row_index is 2-based? We'll compute A-based range: headers row=1; sheet row N => range row N
    # Expect row_values include values for all columns in header order.
    header_range = f"{SHEET_TAB}!A1:1"
    header = sheets_service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=header_range).execute().get("values", [[]])[0]
    if not header:
        st.error("Sheet has no header row.")
        return
    # build range like A{row}:<lastcol>{row}
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

    # Optional: reorder so important columns show first (adjust to your headers)
    # cols_order = ["sheet_row", "id", "timestamp", "reporter", "violator", "category", "description", "coords", "proof_link"]
    # df_display = df_display[[c for c in cols_order if c in df_display.columns]]

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

            # Ensure update function exists in your code
            try:
                update_row_by_sheet_index(sheet_row, row_values)
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
        append_row(row)
        st.success("Report added to sheet.")
        st.rerun()
