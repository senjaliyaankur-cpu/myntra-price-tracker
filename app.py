import streamlit as st
import pandas as pd
import datetime
import os
import sys
import subprocess
import re
import json
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Myntra Price & Stock Tracker", page_icon="🛍️", layout="wide")

SHEET_NAME = "MYNTRA PRICE"

if not os.path.exists("credentials.json") and "gcp_service_account" in st.secrets:
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        with open("credentials.json", "w") as f:
            json.dump(creds_dict, f)
    except Exception as e:
        st.error(f"Credentials file banane me error: {e}")

def fetch_live_style_data(style_id):
    """Myntra API aur HTML scraping fallback ke sath live price, ratings, reviews aur image fetch karta hai."""
    data_out = {"price": "", "star": "", "reviews": "", "image": "", "oos": "NONE", "seller": ""}
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.myntra.com/"
    }
    
    # 1. API Try karna
    try:
        api_url = f"https://www.myntra.com/gateway/v2/product/{style_id}"
        res = requests.get(api_url, headers={"User-Agent": headers["User-Agent"], "Accept": "application/json"}, timeout=8)
        if res.status_code == 200:
            j = res.json()
            price_info = j.get("price", {})
            mrp = price_info.get("mrp", 0)
            discounted = price_info.get("discounted", 0)
            final_price = discounted if discounted > 0 else mrp
            if final_price > 0:
                data_out["price"] = f"₹{final_price}"
            
            rating_info = j.get("rating", {})
            ratings_val = rating_info.get("averageRating", 0)
            review_count = rating_info.get("totalCount", 0)
            if ratings_val:
                data_out["star"] = str(ratings_val)
            if review_count:
                data_out["reviews"] = str(review_count)
                
            search_image = j.get("searchImage")
            if search_image:
                data_out["image"] = search_image
            else:
                views = j.get("views", {})
                if "default" in views:
                    img_info = views["default"].get("image1")
                    if img_info and "secureUrl" in img_info:
                        data_out["image"] = img_info["secureUrl"]
                        
            sizes = j.get("sizes", [])
            if sizes:
                avail_sizes = [s.get("label") for s in sizes if s.get("available")]
                all_sizes = [s.get("label") for s in sizes]
                if not avail_sizes:
                    data_out["oos"] = f"ALL: {', '.join(all_sizes)}"
                else:
                    data_out["oos"] = f"{', '.join(avail_sizes)}"
    except Exception as e:
        print(f"API fetch error: {e}")

    # 2. Agar API se data na mile toh HTML page scrape karna
    if not data_out["price"] or not data_out["image"]:
        try:
            page_url = f"https://www.myntra.com/{style_id}"
            page_res = requests.get(page_url, headers=headers, timeout=10)
            if page_res.status_code == 200:
                soup = BeautifulSoup(page_res.text, 'html.parser')
                meta_img = soup.find("meta", property="og:image")
                if meta_img and meta_img.get("content"):
                    data_out["image"] = meta_img["content"]
                
                match = re.search(r'window\.__myx\s*=\s*(\{.*?\});?</script>', page_res.text, re.DOTALL)
                if match:
                    pdp_data = json.loads(match.group(1)).get("pdpData", {})
                    if not data_out["price"]:
                        price = pdpData.get("price", {}).get("discounted") or pdpData.get("price", {}).get("mrp")
                        if price:
                            data_out["price"] = f"₹{price}"
                    if not data_out["star"]:
                        rating = pdpData.get("ratings", {}).get("averageRating")
                        if rating:
                            data_out["star"] = str(rating)
                    if not data_out["reviews"]:
                        reviews = pdpData.get("ratings", {}).get("totalCount")
                        if reviews:
                            data_out["reviews"] = str(reviews)
        except Exception as e:
            print(f"HTML scrape error: {e}")

    if not data_out["image"]:
        data_out["image"] = f"https://assets.myntassets.com/h_720,q_90,w_540/v1/assets/images/{style_id}/default/image.jpg"
        
    return data_out

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
    with st.spinner("Myntra se latest data aur images fetch ho rahi hain... Please wait..."):
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

if st.sidebar.button("🔄 Refresh & Clear Cache"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")

# Add New Style ID Section in Sidebar
st.sidebar.subheader("➕ Add New Style ID")
new_style_input = st.sidebar.text_input("Enter Style ID or Link")
if st.sidebar.button("Add to Google Sheet"):
    if new_style_input:
        numbers = re.findall(r'\d+', new_style_input)
        if numbers:
            style_id = numbers[-1]
            with st.spinner(f"Fetching live data & adding Style ID {style_id}..."):
                try:
                    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope) if os.path.exists("credentials.json") else ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
                    client = gspread.authorize(creds)
                    sheet = client.open(SHEET_NAME).sheet1
                    
                    all_vals = sheet.get_all_values()
                    headers = all_vals[0] if all_vals else []
                    today_str = datetime.datetime.now().strftime("%d/%m/%Y")
                    
                    style_col_idx = -1
                    for idx, h in enumerate(headers):
                        if "STYLE ID" in str(h).upper() and "LINK" not in str(h).upper():
                            style_col_idx = idx + 1
                            
                    existing_styles = [str(row[style_col_idx - 1]) for row in all_vals[1:]] if style_col_idx != -1 and len(all_vals) > 1 else []
                    
                    if style_id in existing_styles:
                        st.sidebar.warning(f"Style ID {style_id} pehle se sheet me maujood hai!")
                    else:
                        live_data = fetch_live_style_data(style_id)
                        new_row_dict = {}
                        
                        for idx, h in enumerate(headers):
                            h_str = str(h).strip()
                            h_up = h_str.upper()
                            
                            if "STYLE ID" in h_up and "LINK" not in h_up:
                                new_row_dict[idx] = str(style_id)
                            elif "LINK" in h_up:
                                new_row_dict[idx] = f"https://www.myntra.com/{style_id}"
                            elif h_up == "PHOTO" or h_up.startswith("PHOTO_"):
                                new_row_dict[idx] = live_data["image"]
                            else:
                                if today_str in h_str:
                                    if "STAR" in h_up:
                                        new_row_dict[idx] = live_data["star"]
                                    elif "REVIEW" in h_up:
                                        new_row_dict[idx] = live_data["reviews"]
                                    elif "OUT OF STOCK" in h_up:
                                        new_row_dict[idx] = live_data["oos"]
                                    elif "SELLER" in h_up:
                                        new_row_dict[idx] = live_data["seller"]
                                    else:
                                        new_row_dict[idx] = live_data["price"]
                                else:
                                    new_row_dict[idx] = ""
                                    
                        final_row = [new_row_dict.get(i, "") for i in range(len(headers))]
                        sheet.append_row(final_row)
                        
                        st.sidebar.success(f"Style ID {style_id} successfully add ho gaya!")
                        st.cache_data.clear()
                        st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Error adding style: {e}")
        else:
            st.sidebar.error("Kripya valid Style ID ya Link enter karein!")

# Auto-Fill Today's Missing Data (Purane aur naye sabhi styles ke liye)
if st.sidebar.button("⚡ Auto-Fill Today's Missing Data"):
    with st.spinner("Checking and filling missing today's data..."):
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope) if os.path.exists("credentials.json") else ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
            client = gspread.authorize(creds)
            sheet = client.open(SHEET_NAME).sheet1
            
            all_vals = sheet.get_all_values()
            headers = all_vals[0] if all_vals else []
            today_str = datetime.datetime.now().strftime("%d/%m/%Y")
            
            style_col_idx = -1
            for idx, h in enumerate(headers):
                if "STYLE ID" in str(h).upper() and "LINK" not in str(h).upper():
                    style_col_idx = idx
                    break
                    
            if style_col_idx != -1:
                updates = []
                # Cache fetched data per style ID to avoid redundant requests
                fetched_cache = {}
                
                for row_idx, row in enumerate(all_vals[1:], start=2):
                    style_val = row[style_col_idx].strip()
                    # Style ID extract karna (chahe number ho ya link)
                    numbers = re.findall(r'\d+', style_val)
                    if numbers:
                        style_id = numbers[-1]
                        
                        for col_idx, h in enumerate(headers):
                            h_str = str(h).strip()
                            # Check if header contains today's date and cell is empty
                            if today_str in h_str:
                                cell_val = row[col_idx] if col_idx < len(row) else ""
                                if not cell_val.strip():
                                    if style_id not in fetched_cache:
                                        fetched_cache[style_id] = fetch_live_style_data(style_id)
                                    
                                    live_data = fetched_cache[style_id]
                                    h_up = h_str.upper()
                                    val = live_data["price"]
                                    if "STAR" in h_up:
                                        val = live_data["star"]
                                    elif "REVIEW" in h_up:
                                        val = live_data["reviews"]
                                    elif "OUT OF STOCK" in h_up:
                                        val = live_data["oos"]
                                    elif "SELLER" in h_up:
                                        val = live_data["seller"]
                                    
                                    if val:
                                        updates.append(gspread.Cell(row_idx, col_idx + 1, val))
                                        
                if updates:
                    sheet.update_cells(updates)
                    st.sidebar.success(f"Successfully updated {len(updates)} missing fields for today ({today_str})!")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.sidebar.info("Aaj ka data sabhi styles me pehle se maujood hai!")
        except Exception as e:
            st.sidebar.error(f"Error auto-filling data: {e}")

cols = list(df.columns)
dates = []

for c in cols:
    m = re.search(r"(\d{2}/\d{2}/\d{4})", str(c))
    if m:
        d_str = m.group(1)
        if d_str not in dates:
            dates.append(d_str)

dates = sorted(dates, key=lambda x: x[6:10] + x[3:5] + x[0:2])
latest_date = dates[-1] if dates else None

view_mode = st.sidebar.radio("Navigation", ["📊 Clean Price & Stock Table", "⭐ Top Rated Products", "📈 Product Trend Analysis"])

if view_mode == "📊 Clean Price & Stock Table":
    st.subheader("📊 Clean Product Price & History Table")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        search_query = st.text_input("🔍 Search (Style ID, Seller, Size, etc.)", "")
    with col2:
        selected_dates = st.multiselect("📅 Filter by Date Columns", options=dates, default=dates[-1:] if dates else [])
    with col3:
        all_metrics = ["SELLING PRICE", "STAR", "REVIEWS", "OUT OF STOCK", "SELLERS"]
        selected_metrics = st.multiselect("🎯 Select Metrics to Show", options=all_metrics, default=all_metrics)
    with col4:
        stock_filter = st.selectbox("📦 Stock Status Filter", ["All", "In Stock (NONE)", "Out of Stock (ALL)"])
        
    filtered_df = df.copy()
    
    if search_query:
        mask = filtered_df.astype(str).apply(lambda x: x.str.contains(search_query, case=False, na=False)).any(axis=1)
        filtered_df = filtered_df[mask]
        
    if selected_dates:
        base_cols = [c for c in df.columns if not re.search(r"\d{2}/\d{2}/\d{4}", str(c))]
        
        keep_cols = list(base_cols)
        for d in selected_dates:
            for col in df.columns:
                if d in str(col):
                    col_upper = str(col).upper()
                    is_matched = False
                    if "STAR" in col_upper and "STAR" in selected_metrics:
                        is_matched = True
                    elif ("REVIEW" in col_upper) and "REVIEWS" in selected_metrics:
                        is_matched = True
                    elif "OUT OF STOCK" in col_upper and "OUT OF STOCK" in selected_metrics:
                        is_matched = True
                    elif ("SELLER" in col_upper) and "SELLERS" in selected_metrics:
                        is_matched = True
                    elif not any(x in col_upper for x in ["STAR", "REVIEW", "OUT OF STOCK", "SELLER"]) and "SELLING PRICE" in selected_metrics:
                        is_matched = True
                        
                    if is_matched and col not in keep_cols:
                        keep_cols.append(col)
        
        existing_keep_cols = [c for c in keep_cols if c in filtered_df.columns]
        filtered_df = filtered_df[existing_keep_cols]

    if selected_dates and len(selected_dates) > 0 and stock_filter != "All":
        for selected_date in selected_dates:
            oos_cols = [c for c in filtered_df.columns if selected_date in str(c) and "OUT OF STOCK" in str(c).upper()]
            for oos_col in oos_cols:
                if stock_filter == "In Stock (NONE)":
                    filtered_df = filtered_df[filtered_df[oos_col] == "NONE"]
                elif stock_filter == "Out of Stock (ALL)":
                    filtered_df = filtered_df[filtered_df[oos_col].astype(str).str.startswith("ALL")]

    st.metric("Total Products Displayed", len(filtered_df))
    
    multi_tuples = []
    for col in filtered_df.columns:
        col_str = str(col).strip()
        col_upper = col_str.upper()
        
        m = re.search(r"(\d{2}/\d{2}/\d{4})", col_str)
        if m:
            date_part = m.group(1)
            sub_name = col_str.replace(date_part, "").strip(" -_")
            if not sub_name:
                sub_name = "SELLING PRICE"
            multi_tuples.append((date_part, sub_name.upper()))
        else:
            multi_tuples.append(("General Details", col_upper))
            
    filtered_df.columns = pd.MultiIndex.from_tuples(multi_tuples)

    st.dataframe(filtered_df, use_container_width=True, height=600)

elif view_mode == "⭐ Top Rated Products":
    st.subheader("⭐ Top Rated Products Leaderboard")
    col1, col2 = st.columns(2)
    with col1:
        top_n = st.selectbox("Show Top", [10, 20, 50], index=0)
    with col2:
        min_rev = st.number_input("Minimum Reviews", min_value=0, value=10)
        
    if latest_date:
        star_cols = [c for c in df.columns if latest_date in str(c) and "STAR" in str(c).upper()]
        rev_cols = [c for c in df.columns if latest_date in str(c) and "REVIEWS" in str(c).upper()]
        
        if star_cols and rev_cols:
            star_col = star_cols[0]
            rev_col = rev_cols[0]
            top_df = df.copy()
            top_df[star_col] = pd.to_numeric(top_df[star_col], errors='coerce').fillna(0)
            top_df[rev_col] = pd.to_numeric(top_df[rev_col], errors='coerce').fillna(0)
            
            top_df = top_df[(top_df[star_col] > 0) & (top_df[rev_col] >= min_rev)]
            top_df = top_df.sort_values(by=[star_col, rev_col], ascending=[False, False]).head(top_n)
            
            st.dataframe(top_df, use_container_width=True, height=500)

elif view_mode == "📈 Product Trend Analysis":
    st.subheader("📈 Style Price & Rating History Trend")
    style_ids = df['STYLE ID'].astype(str).tolist() if 'STYLE ID' in df.columns else []
    selected_style = st.selectbox("Select Style ID", style_ids)
    
    if selected_style:
        prod_row = df[df['STYLE ID'].astype(str) == str(selected_style)]
        if not prod_row.empty:
            photo_url = ""
            for col in prod_row.columns:
                val = str(prod_row[col].values[0]).strip()
                if val.startswith("http"):
                    photo_url = val
                    break
            if photo_url:
                st.image(photo_url, width=120)
            
            history_data = []
            for d in dates:
                p_cols = [c for c in prod_row.columns if d in str(c) and ("SELLING PRICE" in str(c).upper() or c == d)]
                s_cols = [c for c in prod_row.columns if d in str(c) and "STAR" in str(c).upper()]
                r_cols = [c for c in prod_row.columns if d in str(c) and "REVIEWS" in str(c).upper()]
                
                price_val = prod_row[p_cols[0]].values[0] if p_cols else None
                star_val = prod_row[s_cols[0]].values[0] if s_cols else None
                rev_val = prod_row[r_cols[0]].values[0] if r_cols else None
                
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
