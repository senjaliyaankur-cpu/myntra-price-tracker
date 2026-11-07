import streamlit as st
import pandas as pd
import datetime
import os
import sys
import subprocess
import re
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Myntra Price & Stock Tracker", page_icon="🛍️", layout="wide")

SHEET_NAME = "MYNTRA PRICE"
PHOTO_DIR = "photos"

# Credentials setup for subprocess
if not os.path.exists("credentials.json") and "gcp_service_account" in st.secrets:
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        with open("credentials.json", "w") as f:
            json.dump(creds_dict, f)
    except Exception as e:
        st.error(f"Credentials file banane me error: {e}")

@st.cache_data(ttl=60)
def load_data():
    try:
        if os.path.exists("credentials.json"):
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
            client = gspread.authorize(creds)
        else:
            return None

        spreadsheet = client.open(SHEET_NAME)
        sheet = spreadsheet.sheet1
        rows = sheet.get_all_values()
        
        if not rows or len(rows) < 2:
            return None

        df = pd.DataFrame(rows[1:], columns=rows[0])
        
        new_cols = []
        seen = {}
        for c in df.columns:
            c_str = str(c).strip()
            if c_str in seen:
                seen[c_str] += 1
                new_cols.append(f"{c_str}_{seen[c_str]}")
            else:
                seen[c_str] = 0
                new_cols.append(c_str)
        df.columns = new_cols
        
        if df.empty:
            return None

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
            result = subprocess.run([sys.executable, "myntra_excel_tracker.py"], capture_output=True, text=True, timeout=120)
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
        if oos_col in filtered_df.columns:
            if stock_filter == "In Stock (NONE)":
                filtered_df = filtered_df[filtered_df[oos_col] == "NONE"]
            elif stock_filter == "Out of Stock (ALL)":
                filtered_df = filtered_df[filtered_df[oos_col].astype(str).str.startswith("ALL")]

    st.metric("Total Products Displayed", len(filtered_df))
    
    column_config = {}
    for col in filtered_df.columns:
        if "PHOTO" in col.upper():
            column_config[col] = st.column_config.ImageColumn("Product Photo", width="small")
    
    st.dataframe(
        filtered_df, 
        column_config=column_config,
        use_container_width=True, 
        height=600
    )

elif view_mode == "⭐ Top Rated Products":
    st.subheader("⭐ Top Rated Products Leaderboard")
    col1, col2 = st.columns(2)
    with col1:
        top_n = st.selectbox("Show Top", [10, 20, 50], index=0)
    with col2:
        min_rev = st.number_input("Minimum Reviews", min_value=0, value=10)
        
    if latest_date:
        star_col = f"{latest_date} STAR"
        rev_col = f"{latest_date} REVIEWS"
        
        if star_col in df.columns and rev_col in df.columns:
            top_df = df.copy()
            top_df[star_col] = pd.to_numeric(top_df[star_col], errors='coerce').fillna(0)
            top_df[rev_col] = pd.to_numeric(top_df[rev_col], errors='coerce').fillna(0)
            
            top_df = top_df[(top_df[star_col] > 0) & (top_df[rev_col] >= min_rev)]
            top_df = top_df.sort_values(by=[star_col, rev_col], ascending=[False, False]).head(top_n)
            
            column_config = {}
            for col in top_df.columns:
                if "PHOTO" in col.upper():
                    column_config[col] = st.column_config.ImageColumn("Product Photo", width="small")
            
            st.dataframe(top_df, column_config=column_config, use_container_width=True, height=500)

elif view_mode == "📈 Product Trend Analysis":
    st.subheader("📈 Style Price & Rating History Trend")
    style_ids = df['STYLE ID'].astype(str).tolist() if 'STYLE ID' in df.columns else []
    selected_style = st.selectbox("Select Style ID", style_ids)
    
    if selected_style:
        prod_row = df[df['STYLE ID'].astype(str) == str(selected_style)]
        if not prod_row.empty:
            photo_url = ""
            for col in prod_row.columns:
                if "PHOTO" in col.upper():
                    val = str(prod_row[col].values[0]).strip()
                    if val.startswith("http"):
                        photo_url = val
                        break
            if photo_url:
                st.image(photo_url, width=120)
            
            history_data = []
            for d in dates:
                p_col = d
                s_col = f"{d} STAR"
                r_col = f"{d} REVIEWS"
                
                price_val = prod_row[p_col].values[0] if p_col in prod_row.columns else None
                star_val = prod_row[s_col].values[0] if s_col in prod_row.columns else None
                rev_val = prod_row[r_col].values[0] if r_col in prod_row.columns else None
                
                p_num = float(re.sub(r'[^0-9.]', '', str(price_val))) if pd.notna(price_val) and re.sub(r'[^0-9.]', '', str(price_val)) else None
                
                history_data.append({
                    "Date": d,
                    "Price": p_num,
                    "Star": pd.to_numeric(star_val, errors='coerce'),
                    "Reviews": pd.to_numeric(rev_val, errors='coerce')
                })
                
            hist_df = pd.DataFrame(history_data)
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("#### Price Trend (₹)")
                if not hist_df['Price'].dropna().empty:
                    st.line_chart(hist_df.set_index("Date")['Price'])
            with col2:
                st.markdown("#### Star Rating Trend (★)")
                if not hist_df['Star'].dropna().empty:
                    st.line_chart(hist_df.set_index("Date")['Star'])
            st.markdown("#### Detailed History Table")
            st.dataframe(hist_df, use_container_width=True)
