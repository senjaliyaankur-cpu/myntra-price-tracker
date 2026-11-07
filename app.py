import streamlit as st
import pandas as pd
import datetime
import os
import subprocess
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Myntra Price & Stock Tracker", page_icon="🛍️", layout="wide")

SHEET_NAME = "MYNTRA PRICE"
PHOTO_DIR = "photos"

@st.cache_data(ttl=60)
def load_data():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
        elif os.path.exists("credentials.json"):
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
            client = gspread.authorize(creds)
        else:
            return None

        sheet = client.open(SHEET_NAME).sheet1
        rows = sheet.get_all_values()
        
        if not rows or len(rows) < 2:
            return None

        df = pd.DataFrame(rows[1:], columns=rows[0])
        
        if df.empty:
            return None

        df = df.loc[:, ~df.columns.duplicated()]
        return df
    except Exception as e:
        st.error(f"Google Sheet load karne me error aaya: {e}")
        return None

df = load_data()

if df is None:
    st.error("Google Sheet ('MYNTRA PRICE') load nahi ho payi. Kripya credentials check karein.")
    st.stop()

st.title("🛍️ Myntra Catalog & Price Tracker Dashboard")
st.markdown("Aapke e-commerce products ka live tracking, price fluctuation, stock status aur ratings analysis.")

st.sidebar.header("⚙️ Dashboard Controls")

if st.sidebar.button("🔄 Run Tracker Script (Update Data)", type="primary"):
    with st.spinner("Myntra se latest data fetch ho raha hai... Please wait..."):
        try:
            result = subprocess.run(["python", "myntra_excel_tracker.py"], capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                st.sidebar.success("Data successfully update ho gaya!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.sidebar.error(f"Error: {result.stderr}")
        except Exception as e:
            st.sidebar.error(f"Script run karne me dikkat aayi: {e}")

st.sidebar.markdown("---")

cols = list(df.columns)
date_re = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
dates = []

for c in cols:
    m = date_re.match(str(c).strip())
    if m:
        d_str = m.group(0)
        if d_str not in dates:
            dates.append(d_str)

dates = sorted(dates, key=lambda x: x[6:10] + x[3:5] + x[0:2])
latest_date = dates[-1] if dates else None

view_mode = st.sidebar.radio("Navigation", ["📊 Clean Price & Stock Table", "⭐ Top Rated Products", "📈 Product Trend Analysis"])

if view_mode == "📊 Clean Price & Stock Table":
    st.subheader("📊 Clean Product Price & History Table")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        search_query = st.text_input("🔍 Search (Style ID, Seller, Size, etc.)", "")
    with col2:
        selected_date = st.selectbox("📅 Filter by Date Column", ["All Dates"] + (dates[::-1] if dates else []))
    with col3:
        stock_filter = st.selectbox("📦 Stock Status Filter", ["All", "In Stock (NONE)", "Out of Stock (ALL)"])
        
    filtered_df = df.copy()
    
    if search_query:
        mask = filtered_df.astype(str).apply(lambda x: x.str.contains(search_query, case=False, na=False)).any(axis=1)
        filtered_df = filtered_df[mask]
        
    if selected_date and selected_date != "All Dates":
        oos_col = f"{selected_date} OUT OF STOCK"
