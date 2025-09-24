# NAP.py - Streamlit app that uses Apps Script web app for reads/writes
import streamlit as st
import pandas as pd
import uuid
import requests
from datetime import datetime

# -----------------------
# Config / secrets
# -----------------------
st.set_page_config("NAP Logger", layout="wide")

APP_SCRIPT_URL = st.secrets.get("APP_SCRIPT_URL", None)
APP_SCRIPT_PASSWORD = st.secrets.get("APP_SCRIPT_PASSWORD", None)
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", None)
SHEET_TAB = st.secrets.get("SHEET_TAB_NAME", "Violations")

# Basic checks for required secrets
missing = []
if not APP_SCRIPT_URL:
    missing.append("APP_SCRIPT_URL")
if not APP_SCRIPT_PASSWORD:
    missing.append("APP_SCRIPT_PASSWORD")
if not ADMIN_PASSWORD:
    missing.append("ADMIN_PASSWORD")

if missing:
    st.title("🔧 NAP Logger — configuration required")
    st.error(
        "Your app is missing required secrets: " + ", ".join(missing) +
        ".\n\nIf you're on Streamlit Cloud: open Manage app → Settings → Secrets and add them.\n\n"
        "Locally: create .streamlit/secrets.toml with these keys."
    )
    st.stop()

# -----------------------
# Helpers (Apps Script integration)
# -----------------------
def read_sheet_as_df():
    """
    Read sheet data from the Apps Script doGet endpoint.
    Endpoint returns JSON: { ok: true, data: [ [header...], [row1...], ... ] }
    """
    try:
        resp = requests.get(APP_SCRIPT_URL, timeout=10)
        resp.raise_for_status()
        j = resp.json()
        if not j.get("ok", False):
            st.error(f"Apps Script error: {j.get('error')}")
            return pd.DataFrame()
        data = j.get("data", [])
        if not data or len(data) < 1:
            return pd.DataFrame()
        headers = [str(h) for h in data[0]]
        rows = data[1:]
        # normalize lengths
        max_len = max(len(headers), max((len(r) for r in rows), default=0))
        headers = headers + [f"col_{i}" for i in range(len(headers)+1, max_len+1)]
        normalized = []
        for r in rows:
            if len(r) < max_len:
                normalized.append(r + [""] * (max_len - len(r)))
            else:
                normalized.append(r[:max_len])
        df = pd.DataFrame(normalized, columns=headers)
        return df
    except Exception as e:
        st.error(f"Failed to read from Apps Script: {e}")
        return pd.DataFrame()

def append_row_via_script(row_list):
    """
    row_list expected: [uid, timestamp, reporter, violator, category, description, coords, proofs]
    Posts to Apps Script with action=add.
    """
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
    resp = requests.post(APP_SCRIPT_URL, json=payload, timeout=10)
    resp.raise_for_status()
    j = resp.json() if resp.text else {}
    if not j.get("ok", False):
        raise Exception(f"Apps Script error (add): {j}")

def update_row_via_script(uid, data_dict):
    """
    Update a row by uid via Apps Script. data_dict maps column_name->value.
    """
    payload = {"password": APP_SCRIPT_PASSWORD, "action": "update", "uid": uid}
    # include stringifiable values
    for k, v in data_dict.items():
        if isinstance(v, (str, int, float)) or v is None:
            payload[k] = "" if v is None else str(v)
    resp = requests.post(APP_SCRIPT_URL, json=payload, timeout=10)
    resp.raise_for_status()
    j = resp.json() if resp.text else {}
    if not j.get("ok", False):
        raise Exception(f"Apps Script error (update): {j}")

# -----------------------
# Simple auth (Streamlit-level)
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
st.title("📋 NAP Violation Logs (Google Sheets via Apps Script)")

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
            width="wide",
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
        header_vals = list(df.columns)
        updated_count = 0
        errors = []

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
                try:
                    orig_idx = sheet_row - 2
                    if 0 <= orig_idx < len(df):
                        if "uid" in df.columns:
                            uid_val = df.iloc[orig_idx].get("uid", "")
                except Exception:
                    uid_val = None

            if uid_val:
                # build data dict mapping header->value
                data_dict = {}
                for col in header_vals:
                    data_dict[col] = row.get(col, "")
                # attempt update via Apps Script
                try:
                    update_row_via_script(uid_val, data_dict)
                    updated_count += 1
                except Exception as e:
                    errors.append(f"Row {sheet_row} (uid={uid_val}) update failed: {e}")
            else:
                errors.append(f"Row {sheet_row} skipped: uid not found.")

        if errors:
            for e in errors:
                st.error(e)
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
            append_row_via_script(row)
            st.success("Report added to sheet.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to add report: {e}")
