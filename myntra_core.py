"""
Shared code: Google Sheet connection + Myntra se data fetch + sheet update.
"""

import datetime
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import gspread
import requests

SHEET_NAME = "MYNTRA PRICE"
TZ = ZoneInfo("Asia/Kolkata")
WORKERS = 4  # Reduced workers to avoid rate limiting
DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s*(.*)$")

# Updated Mobile & Modern Headers to bypass Cloudflare
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "Referer": "https://www.myntra.com/",
    "X-Myntra-App": "android",
    "X-Meta-App": "channel=mobile_web",
}
IMG_PREFIX = "https://assets.myntassets.com/h_720,q_90,w_540/v1/"


def _proxies():
    p = (os.environ.get("MYNTRA_PROXY") or "").strip()
    return {"http": p, "https": p} if p else None


def today_str():
    return datetime.datetime.now(TZ).strftime("%d/%m/%Y")


def _local_secrets_toml():
    raw = os.environ.get("GCP_SECRETS_TOML")
    if raw and raw.strip():
        import tomllib
        info = tomllib.loads(raw).get("gcp_service_account")
        if info:
            return dict(info)
    for path in ("secrets.toml", os.path.join(".streamlit", "secrets.toml")):
        if os.path.exists(path):
            try:
                import tomllib
            except ImportError:
                raise RuntimeError("secrets.toml padhne ke liye Python 3.11 ya naya chahiye.")
            with open(path, "rb") as f:
                info = tomllib.load(f).get("gcp_service_account")
            if info:
                return dict(info)
    return None


def get_client(secret_info=None):
    if secret_info:
        return gspread.service_account_from_dict(dict(secret_info))
    if os.path.exists("credentials.json"):
        return gspread.service_account(filename="credentials.json")
    info = _local_secrets_toml()
    if info:
        return gspread.service_account_from_dict(info)
    raw = os.environ.get("GCP_SERVICE_ACCOUNT")
    if raw:
        return gspread.service_account_from_dict(json.loads(raw))
    raise RuntimeError("Google credentials nahi mile.")


def open_sheet(secret_info=None):
    return get_client(secret_info).open(SHEET_NAME).sheet1


def parse_header(h):
    h = str(h).strip()
    m = DATE_RE.match(h)
    if not m:
        return None, h.upper()
    metric = re.sub(r"[\s_\-]+", " ", m.group(2)).strip().upper()
    if metric in ("", "SELLING PRICE", "PRICE"):
        metric = "PRICE"
    elif metric in ("REVIEW", "REVIEWS"):
        metric = "REVIEWS"
    elif metric in ("SELLER", "SELLERS"):
        metric = "SELLERS"
    return m.group(1), metric


def find_col(headers, name, exclude_link=False):
    name = name.upper()
    for i, h in enumerate(headers):
        hu = str(h).strip().upper()
        if hu == name or hu.startswith(name + "_"):
            return i
    for i, h in enumerate(headers):
        hu = str(h).strip().upper()
        if name in hu and not (exclude_link and "LINK" in hu):
            return i
    return -1


def style_id_from(text):
    nums = re.findall(r"\d+", str(text))
    return nums[-1] if nums else ""


def extract_price(text):
    for p in (r'"discountedPrice"\s*:\s*(\d+)', r'"discounted"\s*:\s*(\d+)', r'"mrp"\s*:\s*(\d+)', r'"mrp"\s*:\s*"(\d+)"'):
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None


def extract_rating(text):
    m = re.search(r'"ratings"\s*:\s*\{', text)
    if not m:
        return None
    window = text[m.end(): m.end() + 400]
    a = re.search(r'"averageRating"\s*:\s*([0-9.]+)', window)
    c = re.search(r'"totalCount"\s*:\s*(\d+)', window)
    if a and c:
        return f"{float(a.group(1)):.1f}", c.group(1)
    return None


def normalize_image_url(url):
    if not url:
        return ""
    url = url.replace("\\u002F", "/").replace("\\/", "/")
    m = re.search(r"(assets/images/[^\s\"'<>?]+?\.(?:jpg|jpeg|png|webp))", url, re.I)
    if m:
        return IMG_PREFIX + m.group(1)
    if url.startswith("//"):
        url = "https:" + url
    return url.replace("http://", "https://", 1)


def extract_image(text):
    m = (re.search(r'"imageURL"\s*:\s*"([^"]+)"', text)
         or re.search(r'"searchImage"\s*:\s*"([^"]+)"', text)
         or re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', text))
    return normalize_image_url(m.group(1)) if m else ""


def default_image_url(style_id):
    return f"{IMG_PREFIX}assets/images/{style_id}/default/image.jpg"


def _load_json(text):
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"window\.__myx\s*=\s*(\{.*?\})\s*</script>", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            return None
    return None


def _find_list(obj, key, need_key):
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            v = o.get(key)
            if isinstance(v, list) and v and all(isinstance(x, dict) and need_key in x for x in v):
                return v
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return None


def _size_in_stock(s):
    if "available" in s:
        return bool(s["available"])
    return any((d.get("availableCount") or 0) > 0 for d in (s.get("sizeSellerData") or []))


def extract_stock_sellers(text):
    obj = _load_json(text)
    if obj is None:
        return None
    sizes = _find_list(obj, "sizes", "label")
    if not sizes:
        return None
    out = [str(s["label"]) for s in sizes if not _size_in_stock(s)]
    if not out:
        oos = "NONE"
    elif len(out) == len(sizes):
        oos = "ALL: " + ", ".join(out)
    else:
        oos = ", ".join(out)

    in_stock, everyone = [], []
    for s in sizes:
        for d in (s.get("sizeSellerData") or []):
            n = d.get("sellerName")
            if not n:
                continue
            if n not in everyone:
                everyone.append(n)
            if (d.get("availableCount") or 0) > 0 and n not in in_stock:
                in_stock.append(n)
    if not everyone:
        for d in (_find_list(obj, "sellers", "sellerName") or []):
            n = d.get("sellerName")
            if n and n not in everyone:
                everyone.append(n)
    return oos, ", ".join(in_stock or everyone)


def fetch_style(style_id):
    out = {"price": "", "star": "N/A", "reviews": "N/A", "oos": "N/A", "sellers": "N/A",
           "image": "", "status": "Error Fetching"}
    got_price = False
    
    # Session with standard headers
    session = requests.Session()
    session.headers.update(HEADERS)
    
    urls = [
        f"https://www.myntra.com/gateway/v2/product/{style_id}",
        f"https://www.myntra.com/v1/styles/{style_id}",
        f"https://www.myntra.com/{style_id}"
    ]
    
    for url in urls:
        try:
            r = session.get(url, timeout=12, proxies=_proxies())
            if r.status_code == 200:
                text = r.text
                if not got_price:
                    p = extract_price(text)
                    if p:
                        out["price"], got_price = p, True
                if out["star"] == "N/A":
                    rt = extract_rating(text)
                    if rt:
                        out["star"], out["reviews"] = rt
                if out["oos"] == "N/A":
                    st = extract_stock_sellers(text)
                    if st:
                        out["oos"], out["sellers"] = st[0], (st[1] or "N/A")
                if not out["image"]:
                    out["image"] = extract_image(text)
                if got_price:
                    out["status"] = "OK"
                    return out
        except Exception:
            continue

    if got_price:
        out["status"] = "OK"
    return out


def fetch_many(style_ids, progress=None):
    ids = list(dict.fromkeys(style_ids))
    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for n, (sid, res) in enumerate(zip(ids, ex.map(fetch_style, ids)), 1):
            results[sid] = res
            if progress:
                progress(n, len(ids))
    return results


BLOCK_METRICS = ["PRICE", "STAR", "REVIEWS", "OUT OF STOCK", "SELLERS"]


def ensure_today_block(sheet, headers, date=None):
    date = date or today_str()
    cols = {}
    for i, h in enumerate(headers):
        d, m = parse_header(h)
        if d == date:
            cols[m] = i
    missing = [m for m in BLOCK_METRICS if m not in cols]
    if missing:
        headers = list(headers)
        while headers and not str(headers[-1]).strip():
            headers.pop()
        start = len(headers)
        new_heads = [date if m == "PRICE" else f"{date} {m}" for m in missing]
        need = start + len(new_heads)
        if sheet.col_count < need:
            sheet.add_cols(need - sheet.col_count)
        sheet.update(range_name=gspread.utils.rowcol_to_a1(1, start + 1) + ":" +
                     gspread.utils.rowcol_to_a1(1, need),
                     values=[new_heads], value_input_option="RAW")
        for k, m in enumerate(missing):
            cols[m] = start + k
        headers = headers + new_heads
    return headers, cols


def write_cells(sheet, cells):
    if not cells:
        return
    data = [{"range": gspread.utils.rowcol_to_a1(c.row, c.col), "values": [[c.value]]} for c in cells]
    for i in range(0, len(data), 500):
        sheet.batch_update(data[i:i + 500], value_input_option="USER_ENTERED")


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _cells_for(row_no, cols, res):
    price = _num(res["price"])
    star = float(res["star"]) if re.match(r"^[0-9.]+$", str(res["star"])) else res["star"]
    reviews = _num(res["reviews"])
    vals = {"PRICE": price, "STAR": star, "REVIEWS": reviews,
            "OUT OF STOCK": res["oos"], "SELLERS": res["sellers"]}
    return [gspread.Cell(row_no, cols[m] + 1, vals[m]) for m in BLOCK_METRICS]


def update_today(sheet, progress=None, force=False):
    values = sheet.get_all_values()
    if len(values) < 2:
        return {"products": 0, "fetched": 0, "errors": 0, "photos": 0, "date": today_str(), "error_ids": []}
    headers, rows = values[0], values[1:]
    sid_col = find_col(headers, "STYLE ID", exclude_link=True)
    if sid_col < 0:
        raise RuntimeError("Sheet me 'STYLE ID' naam ka column nahi mila.")
    photo_col = find_col(headers, "PHOTO")

    headers, cols = ensure_today_block(sheet, headers)
    price_col = cols["PRICE"]

    row_sid = {}
    for i, row in enumerate(rows, start=2):
        sid = style_id_from(row[sid_col] if sid_col < len(row) else "")
        if sid:
            row_sid[i] = sid

    def cell(row_no, col):
        row = rows[row_no - 2]
        return row[col].strip() if col < len(row) else ""

    def needs_data(row_no):
        if force:
            return True
        v = cell(row_no, price_col)
        return (not v) or v.startswith("Error") or v == "Price Not Found"

    def needs_photo(row_no):
        if photo_col < 0:
            return False
        v = cell(row_no, photo_col).lower()
        return (not v) or v == "nan" or "default/image.jpg" in v or not v.startswith("http")

    todo = [r for r in row_sid if needs_data(r) or needs_photo(r)]
    results = fetch_many([row_sid[r] for r in todo], progress)

    cells, photos, errors, error_ids = [], 0, 0, []
    for r in todo:
        sid = row_sid[r]
        res = results[sid]
        if needs_data(r):
            if res["price"]:
                cells.extend(_cells_for(r, cols, res))
            else:
                errors += 1
                error_ids.append(f"{sid} ({res['status']})")
        if needs_photo(r):
            url = res["image"]
            if url:
                cells.append(gspread.Cell(r, photo_col + 1, url))
                photos += 1
            elif not cell(r, photo_col):
                cells.append(gspread.Cell(r, photo_col + 1, default_image_url(sid)))
    if cells:
        write_cells(sheet, cells)
    return {"products": len(row_sid), "fetched": len(todo), "errors": errors,
            "photos": photos, "date": today_str(), "error_ids": error_ids[:10]}


def add_style(sheet, text):
    sid = style_id_from(text)
    if not sid:
        return False, "Valid Style ID ya Myntra link daalo."
    values = sheet.get_all_values()
    headers = values[0] if values else []
    sid_col = find_col(headers, "STYLE ID", exclude_link=True)
    if sid_col < 0:
        return False, "Sheet me 'STYLE ID' naam ka column nahi mila."
    if sid in {style_id_from(r[sid_col]) for r in values[1:] if sid_col < len(r)}:
        return False, f"Style ID {sid} pehle se sheet me hai."

    headers, cols = ensure_today_block(sheet, headers)
    res = fetch_style(sid)
    row = [""] * len(headers)
    for i, h in enumerate(headers):
        hu = str(h).strip().upper()
        d, m = parse_header(h)
        if d is None:
            if i == sid_col:
                row[i] = int(sid)
            elif "LINK" in hu:
                row[i] = f"https://www.myntra.com/{sid}"
            elif hu == "PHOTO" or hu.startswith("PHOTO_"):
                row[i] = res["image"] or default_image_url(sid)
    if res["price"]:
        for c in _cells_for(1, cols, res):
            row[c.col - 1] = c.value
    sheet.append_row(row, value_input_option="USER_ENTERED")
    note = "" if res["price"] else f" (price nahi mila: {res['status']})"
    return True, f"Style ID {sid} add ho gaya{note}."
