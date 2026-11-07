import os
import re

import pandas as pd
import streamlit as st

import myntra_core as core

st.set_page_config(page_title="Myntra Price & Stock Tracker", page_icon="🛍️", layout="wide")


# ======================= Google Sheet se data =======================

try:    # Streamlit Secrets me myntra_proxy = "http://user:pass@host:port" ho to use lagao
    if st.secrets.get("myntra_proxy"):
        os.environ["MYNTRA_PROXY"] = str(st.secrets["myntra_proxy"])
except Exception:
    pass


def secret_info():
    """Streamlit Cloud ke Secrets me [gcp_service_account] ho to wo, warna None (credentials.json use hoga)"""
    try:
        return dict(st.secrets["gcp_service_account"])
    except Exception:
        return None


@st.cache_resource(ttl=3000, show_spinner=False)
def get_sheet():
    return core.open_sheet(secret_info())


@st.cache_data(ttl=60, show_spinner="Google Sheet se data aa raha hai...")
def load_data():
    values = get_sheet().get_all_values()
    if len(values) < 2:
        return pd.DataFrame()
    names, seen = [], {}
    for h in values[0]:
        h = str(h).strip()
        if h in seen:               # duplicate header (jaise 2 'SOR TP') ko alag naam do
            seen[h] += 1
            names.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            names.append(h)
    width = len(names)
    rows = [(r + [""] * width)[:width] for r in values[1:]]
    return pd.DataFrame(rows, columns=names)


def to_num(series):
    return pd.to_numeric(series.astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce")


def clear_and_rerun():
    st.cache_data.clear()
    st.rerun()


try:
    df = load_data()
except Exception as e:
    st.error(f"Google Sheet ('{core.SHEET_NAME}') load nahi ho payi: {e}")
    st.info("Check karo: (1) Streamlit Secrets me [gcp_service_account] sahi hai, "
            "(2) sheet ka naam exactly 'MYNTRA PRICE' hai, "
            "(3) sheet service account ki email ke saath Editor access me share ki hai.")
    st.stop()

if df.empty:
    st.warning("Sheet khali hai ya usme data nahi mila.")
    st.stop()

# ---- Columns ko samjho: kaun si base hai, kaun si date wali ----
meta = {c: core.parse_header(c) for c in df.columns}                   # col -> (date, metric)
base_cols = [c for c in df.columns if meta[c][0] is None]
date_cols = [c for c in df.columns if meta[c][0] is not None]
dates = sorted({meta[c][0] for c in date_cols}, key=lambda d: d[6:10] + d[3:5] + d[0:2])
latest_date = dates[-1] if dates else None

sid_col = base_cols[core.find_col(base_cols, "STYLE ID", exclude_link=True)] \
    if core.find_col(base_cols, "STYLE ID", exclude_link=True) >= 0 else None
photo_idx = core.find_col(base_cols, "PHOTO")
photo_col = base_cols[photo_idx] if photo_idx >= 0 else None
link_col = next((c for c in base_cols if "LINK" in c.upper()), None)


def metric_col(date, metric):
    for c in date_cols:
        if meta[c] == (date, metric):
            return c
    return None


def flat_name(c):
    d, m = meta[c]
    return f"{d} {m}" if d else c


def show_table(data, config=None, height=600, row_height=64):
    """st.dataframe ko alag alag streamlit versions par chalane ke liye"""
    base = dict(hide_index=True, height=height, column_config=config or {})
    rh = {"row_height": row_height} if row_height else {}
    for extra in (dict(width="stretch", **rh), dict(use_container_width=True, **rh), dict(use_container_width=True)):
        try:
            return st.dataframe(data, **base, **extra)
        except TypeError:
            continue


def base_config():
    cfg = {}
    if photo_col:
        cfg[photo_col] = st.column_config.ImageColumn("PHOTO", width="small")
    if link_col:
        cfg[link_col] = st.column_config.LinkColumn(link_col, display_text=r"myntra\.com/(\d+)")
    return cfg


# ======================= Sidebar =======================

st.title("🛍️ Myntra Catalog & Price Tracker Dashboard")
st.caption("Products ka price, stock, rating aur reviews ka tracking. Aaj ki date: " + core.today_str())

st.sidebar.header("⚙️ Dashboard Controls")

force = st.sidebar.checkbox("Sab products dobara fetch karo", value=False,
                            help="Band rakho to sirf wahi products fetch honge jinka aaj ka data abhi nahi aaya.")
if st.sidebar.button("⚡ Aaj ka data update karo", type="primary"):
    bar = st.sidebar.progress(0.0, text="Myntra se data aa raha hai...")
    try:
        result = core.update_today(
            get_sheet(), force=force,
            progress=lambda done, total: bar.progress(done / max(total, 1), text=f"{done}/{total} products"))
        bar.empty()
        msg = (f"{result['date']}: {result['fetched']} products check hue, "
               f"{result['photos']} nayi photos, {result['errors']} me data nahi mila.")
        if result["fetched"] and result["errors"] == result["fetched"]:
            msg += " Myntra Streamlit ke server ko block kar raha hai. Data GitHub se update karo: " \
                   "GitHub repo > Actions > 'Update Myntra data' > Run workflow."
            st.session_state["flash"] = ("warning", msg)
        else:
            st.session_state["flash"] = ("success", msg)
        clear_and_rerun()
    except Exception as e:
        bar.empty()
        st.sidebar.error(f"Update me error: {e}")

if "flash" in st.session_state:
    kind, text = st.session_state.pop("flash")
    getattr(st.sidebar, kind)(text)

if st.sidebar.button("🔄 Refresh (cache clear)"):
    clear_and_rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("➕ Naya Style ID jodo")
new_style_input = st.sidebar.text_input("Style ID ya Myntra link")
if st.sidebar.button("Google Sheet me jodo"):
    with st.spinner("Live data la raha hoon..."):
        try:
            ok, text = core.add_style(get_sheet(), new_style_input)
        except Exception as e:
            ok, text = False, f"Error: {e}"
    if ok:
        st.session_state["flash"] = ("success", text)
        clear_and_rerun()
    else:
        st.sidebar.error(text)

view_mode = st.sidebar.radio("Navigation", ["📊 Price & Stock Table", "⭐ Top Rated Products",
                                            "📈 Product Trend Analysis"])

# ======================= 1. Table =======================

if view_mode == "📊 Price & Stock Table":
    st.subheader("📊 Product Price & Stock Table")
    all_metrics = []
    for c in date_cols:
        if meta[c][1] not in all_metrics:
            all_metrics.append(meta[c][1])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        search = st.text_input("🔍 Search (Style ID, Seller, Size...)", "")
    with c2:
        sel_dates = st.multiselect("📅 Dates", options=dates, default=dates[-1:])
    with c3:
        sel_metrics = st.multiselect("🎯 Columns dikhao", options=all_metrics, default=all_metrics)
    with c4:
        stock_filter = st.selectbox("📦 Stock filter (sabse nayi date ka)",
                                    ["All", "Koi size out nahi (NONE)", "Kuch sizes out", "Saare sizes out (ALL)"])

    view = df
    if search:
        mask = view.apply(lambda col: col.astype(str).str.contains(search, case=False, na=False, regex=False)).any(axis=1)
        view = view[mask]

    if stock_filter != "All" and sel_dates:
        oos = metric_col(max(sel_dates, key=lambda d: d[6:10] + d[3:5] + d[0:2]), "OUT OF STOCK")
        if oos:
            v = view[oos].astype(str).str.strip().str.upper()
            if stock_filter.startswith("Koi"):
                view = view[v == "NONE"]
            elif stock_filter.startswith("Kuch"):
                view = view[(v != "NONE") & (v != "") & (~v.str.startswith("ALL")) & (v != "N/A")]
            else:
                view = view[v.str.startswith("ALL")]

    show = list(base_cols)
    sel_dates = sorted(sel_dates, key=lambda d: d[6:10] + d[3:5] + d[0:2])
    for d in sel_dates:
        for c in date_cols:
            if meta[c][0] == d and meta[c][1] in sel_metrics:
                show.append(c)
    view = view[show].copy()
    cfg = base_config()
    for c in show:
        if meta[c][0]:
            cfg[c] = st.column_config.TextColumn(flat_name(c))
    st.metric("Products", len(view))
    show_table(view, cfg)

# ======================= 2. Top rated =======================

elif view_mode == "⭐ Top Rated Products":
    st.subheader("⭐ Top Rated Products (pichle 7 din ki history ke saath)")
    c1, c2 = st.columns(2)
    top_n = c1.selectbox("Top", [10, 20, 50], index=0)
    min_rev = c2.number_input("Minimum reviews", min_value=0, value=10)

    star_c = metric_col(latest_date, "STAR") if latest_date else None
    rev_c = metric_col(latest_date, "REVIEWS") if latest_date else None
    if not (star_c and rev_c):
        st.info("Abhi star / reviews ka data nahi hai. Pehle 'Aaj ka data update karo' dabao.")
    else:
        top = pd.DataFrame(index=df.index)
        if photo_col:
            top["PHOTO"] = df[photo_col]
        if link_col:
            top["LINK"] = df[link_col]
        top["STYLE ID"] = df[sid_col] if sid_col else ""
        price_c = metric_col(latest_date, "PRICE")
        if price_c:
            top["PRICE"] = df[price_c]
        top["STAR"] = to_num(df[star_c])
        top["REVIEWS"] = to_num(df[rev_c])
        for d in dates[-7:]:
            sc = metric_col(d, "STAR")
            if sc:
                top[f"★ {d[:5]}"] = to_num(df[sc])
        top = top[(top["STAR"] > 0) & (top["REVIEWS"] >= min_rev)]
        top = top.sort_values(["STAR", "REVIEWS"], ascending=False).head(top_n).reset_index(drop=True)
        top.insert(0, "#", range(1, len(top) + 1))
        cfg = {"PHOTO": st.column_config.ImageColumn("PHOTO", width="small"),
               "LINK": st.column_config.LinkColumn("LINK", display_text=r"myntra\.com/(\d+)"),
               "STAR": st.column_config.NumberColumn("STAR", format="%.1f"),
               "REVIEWS": st.column_config.NumberColumn("REVIEWS", format="%d")}
        if top.empty:
            st.info("Is 'Minimum reviews' ke saath koi product nahi mila. Minimum reviews kam karke dekho.")
        else:
            show_table(top, cfg, height=min(80 + 64 * len(top), 750))

# ======================= 3. Trend =======================

else:
    st.subheader("📈 Style ka Price & Rating History Trend")
    if not sid_col:
        st.warning("Sheet me 'STYLE ID' column nahi mila.")
        st.stop()
    options = [s for s in df[sid_col].astype(str).tolist() if s.strip()]
    chosen = st.selectbox("Style ID chuno", options)
    row = df[df[sid_col].astype(str) == str(chosen)].iloc[0]
    if photo_col and str(row[photo_col]).startswith("http"):
        st.image(str(row[photo_col]), width=140)

    hist = []
    for d in dates:
        def val(metric):
            c = metric_col(d, metric)
            return to_num(pd.Series([row[c]])).iloc[0] if c else None
        hist.append({"Date": d, "Price": val("PRICE"), "Star": val("STAR"), "Reviews": val("REVIEWS")})
    hist_df = pd.DataFrame(hist)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Price Trend (₹)")
        if hist_df["Price"].notna().any():
            st.line_chart(hist_df.set_index("Date")["Price"])
    with c2:
        st.markdown("#### Star Rating Trend (★)")
        if hist_df["Star"].notna().any():
            st.line_chart(hist_df.set_index("Date")["Star"])
    st.markdown("#### Detailed History")
    show_table(hist_df, height=min(80 + 36 * len(hist_df), 500), row_height=None)
