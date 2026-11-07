"""
MYNTRA EXCEL TRACKER  (aapke computer par chalti hai)

Ek hi Excel file (MYNTRA PRICE.xlsx) me sab kuch:
  Column D          -> PHOTO (asli photo, 72 size). Jis row me pehle se photo hai wahan kuch nahi hota.
  Column E se aage  -> har din ke 5 columns:
        [aaj ki date]                selling price
        [aaj ki date] STAR           star rating
        [aaj ki date] REVIEWS        total ratings/reviews count
        [aaj ki date] OUT OF STOCK   jo sizes stock out hain (NONE = koi nahi, ALL: ... = sab)
        [aaj ki date] SELLERS        seller ke naam (1 se zyada ho to comma se alag)
        [aaj ki date] PAGE           TYPE column ke hisaab se listing me product kis page par hai (LISTINGS dekho)
        [aaj ki date] POSITION       us page par kaunsi position par hai
        [aaj ki date] BRAND PAGE     KALINI brand filter lagake listing me kis page par hai
        [aaj ki date] BRAND POSITION us page par kaunsi position par hai
  (saare size stock out wale products ka rank nahi dhundha jata, wahan "Stock out" likha aata hai)
  Excel me "TYPE" naam ka column hona chahiye: KURTAS / KURTA SET / LEHENGA CHOLI

Kaise chalana hai:
1. Ek baar:  pip install requests openpyxl pillow
2. "MYNTRA PRICE.xlsx" is script ke folder me rakho (Excel me khuli ho to band kar do).
   Agar xlsx nahi hai lekin "MYNTRA PRICE.csv" hai, to pehli baar script us CSV se xlsx bana legi.
3. PowerShell me:  python myntra_excel_tracker.py
4. Roz chalane par naye din ke columns judte jayenge. Same din dobara chalao to usi din wale update hote hain.
"""

import csv
import datetime
import io
import json
import os
import re
import shutil
import time
import requests
from urllib.parse import quote, urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter
    from PIL import Image as PILImage
except ImportError:
    print("Pehle ye chalao:  pip install requests openpyxl pillow")
    raise SystemExit(1)

FILE = "MYNTRA PRICE.xlsx"
CSV_FILE = "MYNTRA PRICE.csv"
PHOTO_DIR = "photos"
IMG_BOX = 72          # photo ka size (pixels) - zyada chahiye to 100, 120 kar do
WORKERS = 10          # ek saath kitne products (kam karo agar errors aayein)
BLOCK = 9             # har date ke columns: price, star, reviews, out of stock, sellers, page, position, brand page, brand position
# STYLE ID, PHOTO, TYPE columns ab header ke naam se dhundhe jate hain (kahin bhi ho sakte hain)
HTML_FILE = FILE.replace(".xlsx", ".html")   # Chrome me dekhne wali file
TOP10_FILE = "MYNTRA TOP10.html"             # Top rating wala page

# ---- Listing page rank (kaunse page / position par product hai) ----
# Excel ke "TYPE" column me jo likha hai (KURTAS / KURTA SET / LEHENGA CHOLI) us hisaab se link chuna jata hai
LISTINGS = {
    "KURTAS":        "https://www.myntra.com/women-kurtas-kurtis-suits?sort=popularity&f=Categories%3AKurtas",
    "KURTA SET":     "https://www.myntra.com/women-kurtas-kurtis-suits?sort=popularity&f=Categories%3AKurta%20Sets",
    "LEHENGA CHOLI": "https://www.myntra.com/lehenga-choli?sort=popularity&f=Gender%3Amen%20women%2Cwomen",
}
BRAND = "KALINI"      # brand filter laga ke bhi alag se rank nikalta hai
MAX_PAGES = 40        # category listing me kitne page tak dhundna hai
MAX_BRAND_PAGES = 20  # brand filter wali listing me kitne page tak
PAGE_ROWS = 50        # Myntra ek page par kitne products dikhata hai
PAGE_BATCH = 4        # ek saath kitne pages fetch hon

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json,text/html,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "x-meta-app": "channel=web",
}

PRICE_PATTERNS = [
    r'"discountedPrice"\s*:\s*(\d+)',
    r'"discounted"\s*:\s*(\d+)',
    r'"mrp"\s*:\s*(\d+)',
]


# ================= Myntra se data nikalna =================

def extract_price(text):
    for p in PRICE_PATTERNS:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None


def extract_rating(text):
    """ "ratings": {...} block ke andar se averageRating aur totalCount nikalta hai """
    m = re.search(r'"ratings"\s*:\s*\{', text)
    if not m:
        return None
    window = text[m.end(): m.end() + 400]
    a = re.search(r'"averageRating"\s*:\s*([0-9.]+)', window)
    c = re.search(r'"totalCount"\s*:\s*(\d+)', window)
    if a and c:
        return f"{float(a.group(1)):.1f}", c.group(1)
    return None


def extract_image(text):
    m = (re.search(r'"imageURL"\s*:\s*"([^"]+)"', text)
         or re.search(r'"searchImage"\s*:\s*"([^"]+)"', text)
         or re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', text))
    if not m:
        return None
    url = m.group(1).replace("\\u002F", "/").replace("\\/", "/")
    url = (url.replace("($height)", "240")
              .replace("($qualityPercentage)", "80")
              .replace("($width)", "180"))
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def load_json(text):
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r'window\.__myx\s*=\s*(\{.*?\})\s*</script>', text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            return None
    return None


def find_list(obj, key, need_key):
    """JSON me pehli aisi list dhundta hai jo `key` ke neeche ho aur jiske items me `need_key` ho"""
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


def size_in_stock(s):
    if "available" in s:
        return bool(s["available"])
    data = s.get("sizeSellerData") or []
    return any((d.get("availableCount") or 0) > 0 for d in data)


def extract_stock_sellers(text):
    obj = load_json(text)
    if obj is None:
        return None
    sizes = find_list(obj, "sizes", "label")
    if not sizes:
        return None

    out = [str(s["label"]) for s in sizes if not size_in_stock(s)]
    if not out:
        oos = "NONE"
    elif len(out) == len(sizes):
        oos = "ALL: " + ", ".join(out)
    else:
        oos = ", ".join(out)

    names_stock, names_all = [], []
    for s in sizes:
        for d in (s.get("sizeSellerData") or []):
            n = d.get("sellerName")
            if not n:
                continue
            if n not in names_all:
                names_all.append(n)
            if (d.get("availableCount") or 0) > 0 and n not in names_stock:
                names_stock.append(n)
    if not names_all:
        for d in (find_list(obj, "sellers", "sellerName") or []):
            n = d.get("sellerName")
            if n and n not in names_all:
                names_all.append(n)
    names = names_stock or names_all
    return oos, ", ".join(names)


def get_info(style_id, need_image):
    urls = [
        f"https://www.myntra.com/gateway/v2/product/{style_id}",  # halka JSON
        f"https://www.myntra.com/{style_id}",                      # fallback page
    ]
    price = None
    rating = None
    reviews = None
    image = None
    oos = None
    sellers = None
    status = "Error Fetching"
    for attempt in range(2):
        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code == 200:
                    if price is None:
                        p = extract_price(r.text)
                        if p:
                            price = "₹" + p
                        else:
                            status = "Price Not Found"
                    if rating is None:
                        rt = extract_rating(r.text)
                        if rt:
                            rating, reviews = rt
                    if need_image and image is None:
                        image = extract_image(r.text)
                    if oos is None:
                        st = extract_stock_sellers(r.text)
                        if st:
                            oos, sellers = st
                    if price and rating and oos is not None and (image or not need_image):
                        return price, rating, reviews, image or "", oos, sellers or "N/A"
                else:
                    status = f"Error {r.status_code}"
            except requests.RequestException:
                status = "Error Fetching"
        if price:
            break  # price mil gaya; baaki nahi mila to N/A
        time.sleep(1)
    if price is None:
        return status, "", "", image or "", "", ""
    return (price, rating or "N/A", reviews or "N/A", image or "",
            oos if oos is not None else "N/A", (sellers or "N/A"))


# ================= Listing page par rank (page + position) =================

def type_key(v):
    """TYPE column ki value ko LISTINGS ke naam me badalta hai"""
    t = re.sub(r"[^A-Z]", "", str(v or "").upper())
    if "LEHENGA" in t or "CHOLI" in t:
        return "LEHENGA CHOLI"
    if "SET" in t:
        return "KURTA SET"
    if t.startswith("KURT"):
        return "KURTAS"
    return None


def listing_page_ids(page, listing_url, brand=None):
    """Listing ke ek page ke product IDs, usi order me jis order me Myntra dikhata hai"""
    u = urlparse(listing_url)
    qs = {k: v[0] for k, v in parse_qs(u.query).items()}
    if brand:
        qs["f"] = (qs["f"] + "::" if qs.get("f") else "") + "Brand:" + brand
    api = "https://www.myntra.com/gateway/v2/search/" + u.path.strip("/")
    api_params = dict(qs, rows=PAGE_ROWS, o=(page - 1) * PAGE_ROWS, p=page, plaEnabled="false")
    html_params = dict(qs, p=page)
    for url, params in ((api, api_params), ("https://www.myntra.com" + u.path, html_params)):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            ids = list(dict.fromkeys(re.findall(r'"productId"\s*:\s*(\d+)', r.text)))
            if ids:
                return ids
        except requests.RequestException:
            continue
    return []


def find_ranks(listing_url, wanted, max_pages, brand=None):
    """{style_id: (page, position)} - jo product max_pages tak nahi mila use dict me nahi daalta.
    Returns (ranks, pages_scanned, first_page_ok)"""
    wanted = set(wanted)
    ranks, seen_ids = {}, set()
    scanned = 0
    first_ok = True
    page = 1
    while page <= max_pages and len(ranks) < len(wanted):
        batch = list(range(page, min(page + PAGE_BATCH, max_pages + 1)))
        with ThreadPoolExecutor(max_workers=PAGE_BATCH) as ex:
            results = list(ex.map(lambda pg: listing_page_ids(pg, listing_url, brand), batch))
        stop = False
        for pg, ids in zip(batch, results):
            if not ids:
                if pg == 1:
                    first_ok = False
                stop = True      # page khali = list khatam ya Myntra ne roka
                break
            scanned = pg
            pos = 0
            for pid in ids:
                if pid in seen_ids:
                    continue     # same product dobara aaye (sponsored) to pehli jagah hi maani jaye
                seen_ids.add(pid)
                pos += 1
                if pid in wanted and pid not in ranks:
                    ranks[pid] = (pg, pos)
        if stop:
            break
        page = batch[-1] + 1
    return ranks, scanned, first_ok


def miss_text(scanned, max_pages, ok):
    if not ok:
        return "N/A"
    return f"Not in first {max_pages} pages" if scanned >= max_pages else "Not found"


def download_photo(item):
    sid, url = item
    path = os.path.join(PHOTO_DIR, f"{sid}.jpg")
    if os.path.exists(path):
        return sid, path, None
    if not url:
        return sid, None, "photo ka link nahi mila"
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://www.myntra.com/"}, timeout=20)
        if r.status_code != 200:
            return sid, None, f"HTTP {r.status_code}"
        PILImage.open(io.BytesIO(r.content)).convert("RGB").save(path, "JPEG", quality=90)
        return sid, path, None
    except Exception as e:  # koi bhi dikkat ho to us photo ko skip karo
        return sid, None, str(e)[:80]


# ================= Excel helpers =================

RUPEE = re.compile(r"^₹\s*([\d,]+)$")
NUM = re.compile(r"^\d+(\.\d+)?$")
DATE_HDR = re.compile(r"^\d{2}/\d{2}/\d{4}")


def cell_empty(v):
    return v is None or str(v).strip() == ""


def set_text(cell, v):
    cell.value = v
    cell.number_format = "@"


def set_value(cell, v):
    """Text me likha number/rupee ko asli number bana deta hai (Excel me calculation ho sake)"""
    if isinstance(v, str):
        s = v.strip()
        m = RUPEE.match(s)
        if m:
            cell.value = int(m.group(1).replace(",", ""))
            cell.number_format = '"₹"#,##0'
            return
        if NUM.match(s):
            if "." in s:
                cell.value = float(s)
                cell.number_format = "0.0"
            else:
                cell.value = int(s)
            return
    cell.value = v


def header_text(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%d/%m/%Y")
    return "" if v is None else str(v).strip()


def sid_of(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s if s.isdigit() else ""


def load_book():
    if os.path.exists(FILE):
        return load_workbook(FILE)
    if os.path.exists(CSV_FILE):
        print(f"{FILE} nahi mili, {CSV_FILE} se bana raha hoon...")
        wb = Workbook()
        ws = wb.active
        ws.title = "MYNTRA PRICE"
        with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
            csv_rows = list(csv.reader(f))
        hdr = [h.strip().upper() for h in csv_rows[0]] if csv_rows else []
        csv_photo = hdr.index("PHOTO") + 1 if "PHOTO" in hdr else None
        for ri, row in enumerate(csv_rows, start=1):
            for ci, v in enumerate(row, start=1):
                if v == "":
                    continue
                c = ws.cell(ri, ci)
                if ri == 1:
                    set_text(c, v)
                elif ci == csv_photo:
                    c.value = v          # photo ka link/formula, neeche saaf kiya jayega
                else:
                    set_value(c, v)
        return wb
    print(f"'{FILE}' (ya '{CSV_FILE}') is folder me nahi mili. File ko script ke saath wale folder me rakho.")
    raise SystemExit(1)


def image_rows(ws, photo_col):
    """Un rows ke number jinme PHOTO column me pehle se photo lagi hai"""
    rows = set()
    for img in getattr(ws, "_images", []):
        try:
            a = img.anchor._from
            if a.col == photo_col - 1:
                rows.add(a.row + 1)
        except AttributeError:
            pass
    return rows


def repair_duplicate_photo(ws):
    """Do 'PHOTO' column ban gaye hon (purani script ne TYPE column ke baad extra column daal diya tha)
    to extra wala hata deta hai, taaki photos sahi column me hi rahein."""
    cols = [c for c in range(1, ws.max_column + 1) if header_text(ws.cell(1, c).value).upper() == "PHOTO"]
    if len(cols) < 2:
        return
    img_cols = []
    for img in getattr(ws, "_images", []):
        try:
            img_cols.append(img.anchor._from.col + 1)
        except AttributeError:
            pass
    keep = cols[0]
    if img_cols:
        want = max(set(img_cols), key=img_cols.count)
        for k in cols:
            if k - sum(1 for c in cols if c < k) == want:   # baaki hatane ke baad ye column photo wale column par aayega
                keep = k
                break
    for c in sorted((c for c in cols if c != keep), reverse=True):
        ws.delete_cols(c)
    print(f"Extra PHOTO column hata diya ({len(cols) - 1} column).")


# ================= Chrome me dekhne wali HTML file =================

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MYNTRA PRICE</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;height:100vh;display:flex;flex-direction:column;font-family:"Segoe UI",Arial,sans-serif;background:#f4f5f7;color:#222}
  .bar{background:#fff;border-bottom:1px solid #ddd;padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .bar h1{font-size:18px;margin:0 8px 0 0}
  input[type=search]{padding:7px 10px;font-size:14px;border:1px solid #bbb;border-radius:6px;width:240px}
  .btn{padding:7px 12px;font-size:14px;border:1px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}
  .btn:hover{background:#f3f4f6}
  .count{color:#666;font-size:13px}
  .dd{position:relative}
  .panel{display:none;position:absolute;top:calc(100% + 4px);left:0;z-index:50;background:#fff;border:1px solid #ccc;border-radius:8px;padding:8px 10px;box-shadow:0 6px 20px rgba(0,0,0,.15);min-width:230px;max-height:65vh;overflow:auto}
  .dd.open .panel{display:block}
  .panel label{display:flex;gap:8px;align-items:center;padding:4px 2px;font-size:14px;cursor:pointer;white-space:nowrap}
  .panel .row{display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap}
  .panel .row button{padding:4px 10px;font-size:12.5px;border:1px solid #bbb;border-radius:5px;background:#f9fafb;cursor:pointer}
  .panel h4{margin:10px 0 2px;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:.04em}
  .wrap{flex:1;overflow:auto}
  table{border-collapse:separate;border-spacing:0;background:#fff;table-layout:fixed}
  th{position:sticky;background:#1f2937;color:#fff;padding:8px 22px 8px 10px;font-size:12.5px;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;z-index:3;border-right:1px solid #374151}
  th.r1{top:0;height:34px}
  th.r2{top:34px;height:32px}
  th.grp{background:#111827;text-align:center;padding:8px 10px;font-size:13px;letter-spacing:.02em;border-bottom:1px solid #374151}
  th.gs{border-left:2px solid #6b7280}
  td.gs{border-left:2px solid #d1d5db}
  th .x{position:absolute;right:10px;top:50%;transform:translateY(-50%);cursor:pointer;opacity:.55;font-size:15px;font-weight:400}
  th .x:hover{opacity:1;color:#fca5a5}
  th .rs{position:absolute;right:0;top:0;bottom:0;width:7px;cursor:col-resize}
  th .rs:hover{background:rgba(96,165,250,.75)}
  td{padding:6px 10px;border-bottom:1px solid #eee;font-size:13.5px;vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  td.wrapc{white-space:normal;word-break:break-word}
  td a{color:#1d4ed8;text-decoration:none}
  td a:hover{text-decoration:underline}
  .bar a.nav{padding:7px 12px;border:1px solid #bbb;border-radius:6px;color:#1d4ed8;text-decoration:none;font-size:14px;background:#fff}
  .bar a.nav:hover{background:#eff6ff}
  tr:hover td{background:#eef6ff}
  td img{height:72px;width:auto;max-width:100%;border-radius:4px;cursor:zoom-in;display:block}
  td.price{font-weight:700}
  td.good{color:#15803d;font-weight:600}
  td.bad{color:#b91c1c;font-weight:600}
  td.warn{color:#b45309;font-weight:600}
  td.empty{padding:30px;color:#666;white-space:normal}
</style></head>
<body>
<div class="bar">
  <h1>MYNTRA PRICE</h1>
  <a class="nav" href="__TOP_LINK__">&#9733; Top 10 Rating</a>
  <input id="q" type="search" placeholder="Search (style id, seller, size...)">
  <div class="dd"><button class="btn" id="btnDate"></button><div class="panel" id="panelDate"></div></div>
  <div class="dd"><button class="btn" id="btnCols">Columns &#9662;</button><div class="panel" id="panelCols"></div></div>
  <span class="count" id="count"></span>
</div>
<div class="wrap"><table id="tbl"><colgroup id="cg"></colgroup><thead id="head"></thead><tbody id="body"></tbody></table></div>
<script>
const DATA = __DATA__;
const H = DATA.headers, R = DATA.rows, PHOTO = DATA.photoCol;
const DATE_RE = /^(\d{2})\/(\d{2})\/(\d{4})/;
const dkey = s => { const m = s.match(DATE_RE); return m ? m[3] + m[2] + m[1] : ''; };
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const $ = id => document.getElementById(id);
const q = $('q'), tbl = $('tbl'), head = $('head'), body = $('body');
const panelDate = $('panelDate'), panelCols = $('panelCols'), btnDate = $('btnDate');

// har column ki info: date wale columns me date aur metric (PRICE/STAR/...) alag
const info = H.map((h, i) => {
  const m = h.match(DATE_RE);
  return m ? { i, date: m[0], metric: (h.slice(m[0].length).trim() || 'PRICE'), text: h }
           : { i, date: null, metric: null, text: h };
});
const dates = [...new Set(info.filter(c => c.date).map(c => c.date))].sort((a, b) => dkey(a) < dkey(b) ? -1 : 1);
const metrics = [...new Set(info.filter(c => c.date).map(c => c.metric))];
const latest = dates.length ? dates[dates.length - 1] : null;

// settings (chhoti-badi width aur hide yaad rahegi)
const S = { dates: latest ? [latest] : [], hidden: {}, widths: {} };
try {
  const v = JSON.parse(localStorage.getItem('myntraView') || '{}');
  S.hidden = v.hidden || {}; S.widths = v.widths || {};
} catch (e) {}
const save = () => { try { localStorage.setItem('myntraView', JSON.stringify({ hidden: S.hidden, widths: S.widths })); } catch (e) {} };

const bk = i => 'b' + i;
const hideKeys = c => c.date ? ['m:' + c.metric, 'c:' + c.text] : [bk(c.i)];
const isHidden = c => hideKeys(c).some(k => S.hidden[k]);
const wKey = c => c.date ? 'm:' + c.metric : bk(c.i);
const DEF_BASE = [100, 80, 220, 100];
const DEF_M = { 'PRICE': 95, 'STAR': 80, 'REVIEWS': 95, 'OUT OF STOCK': 170, 'SELLERS': 230, 'PAGE': 70, 'POSITION': 80, 'BRAND PAGE': 90, 'BRAND POSITION': 110 };
const widthOf = c => S.widths[wKey(c)] || (c.date ? (DEF_M[c.metric] || 110) : (DEF_BASE[c.i] || 110));

let cols = [];
function computeCols() {
  cols = info.filter(c => (!c.date || S.dates.includes(c.date)) && !isHidden(c));
  cols.forEach((c, k) => { c.gs = !!c.date && (k === 0 || cols[k - 1].date !== c.date); });
}

function renderHead() {
  let r1 = '', r2 = '', k = 0;
  while (k < cols.length) {
    const c = cols[k];
    if (!c.date) {
      r1 += '<th class="r1" rowspan="2" title="' + esc(c.text) + '">' + esc(c.text) +
            '<b class="x" data-hide="' + bk(c.i) + '" title="Hide">&times;</b><i class="rs" data-w="' + wKey(c) + '"></i></th>';
      k++; continue;
    }
    let j = k; while (j < cols.length && cols[j].date === c.date) j++;
    r1 += '<th class="r1 grp gs" colspan="' + (j - k) + '">' + esc(c.date) + '</th>';
    for (let t = k; t < j; t++) {
      const d = cols[t];
      r2 += '<th class="r2' + (d.gs ? ' gs' : '') + '">' + esc(d.metric) +
            '<b class="x" data-hide="c:' + esc(d.text) + '" title="Hide">&times;</b><i class="rs" data-w="' + wKey(d) + '"></i></th>';
    }
    k = j;
  }
  head.innerHTML = '<tr>' + r1 + '</tr><tr>' + r2 + '</tr>';
}

function applyWidths() {
  $('cg').innerHTML = cols.map(c => '<col style="width:' + widthOf(c) + 'px">').join('');
  tbl.style.width = cols.reduce((s, c) => s + widthOf(c), 0) + 'px';
}

function cell(c, v) {
  const g = c.gs ? ' gs' : '';
  if (c.i === PHOTO) {
    return '<td class="c' + g + '">' + (v ? '<img src="' + esc(v) + '" loading="lazy" onclick="window.open(this.src)">' : '') + '</td>';
  }
  if (c.date) {
    if (c.metric === 'PRICE') return '<td class="price' + g + '">' + esc(v) + '</td>';
    if (c.metric === 'STAR') return '<td class="c' + g + '">' + (v && v !== 'N/A' ? '&#9733; ' + esc(v) : esc(v)) + '</td>';
    if (c.metric === 'OUT OF STOCK') return '<td class="wrapc ' + (v.startsWith('ALL') ? 'bad' : (v === 'NONE' ? 'good' : 'warn')) + g + '">' + esc(v) + '</td>';
    if (c.metric === 'SELLERS') return '<td class="wrapc' + g + '">' + esc(v) + '</td>';
  }
  if (/^https?:\/\//i.test(v)) return '<td class="c' + g + '" title="' + esc(v) + '"><a href="' + esc(v) + '" target="_blank" rel="noopener">' + esc(v) + '</a></td>';
  return '<td class="c' + g + '" title="' + esc(v) + '">' + esc(v) + '</td>';
}

function renderBody() {
  const term = q.value.trim().toLowerCase();
  const rows = R.filter(r => !term || r.join(' ').toLowerCase().includes(term));
  if (!cols.length) {
    body.innerHTML = '<tr><td class="empty">Koi column select nahi hai. "Columns" me se columns chalu karo.</td></tr>';
  } else {
    body.innerHTML = rows.map(r => '<tr>' + cols.map(c => cell(c, r[c.i])).join('') + '</tr>').join('');
  }
  $('count').textContent = rows.length + ' / ' + R.length + ' products';
}

function dateLabel() {
  const n = S.dates.length;
  return 'Dates: ' + (n === 0 ? 'none' : n === 1 ? S.dates[0] : n + ' selected') + ' &#9662;';
}
function buildDatePanel() {
  panelDate.innerHTML =
    '<div class="row"><button data-d="latest">Latest</button><button data-d="all">All</button><button data-d="none">None</button></div>' +
    dates.slice().reverse().map(d => '<label><input type="checkbox" value="' + d + '"' + (S.dates.includes(d) ? ' checked' : '') + '> ' + d + '</label>').join('');
  btnDate.innerHTML = dateLabel();
}
function buildColPanel() {
  const bases = info.filter(c => !c.date);
  panelCols.innerHTML =
    '<div class="row"><button data-a="showall">Show all</button><button data-a="resetw">Reset widths</button></div>' +
    '<h4>Columns</h4>' +
    bases.map(c => '<label><input type="checkbox" data-hk="' + bk(c.i) + '"' + (S.hidden[bk(c.i)] ? '' : ' checked') + '> ' +
                   (c.i < 26 ? String.fromCharCode(65 + c.i) + ' - ' : '') + esc(c.text) + '</label>').join('') +
    (metrics.length ? '<h4>Har date ke columns</h4>' : '') +
    metrics.map(m => '<label><input type="checkbox" data-hk="m:' + esc(m) + '"' + (S.hidden['m:' + m] ? '' : ' checked') + '> ' + esc(m) + '</label>').join('');
}

function refresh() {
  computeCols(); renderHead(); applyWidths(); renderBody();
  btnDate.innerHTML = dateLabel();
}

// ---- date select (multiple) ----
panelDate.addEventListener('click', e => {
  const b = e.target.closest('button[data-d]'); if (!b) return;
  const m = b.dataset.d;
  S.dates = m === 'latest' ? (latest ? [latest] : []) : m === 'all' ? dates.slice() : [];
  buildDatePanel(); refresh();
});
panelDate.addEventListener('change', e => {
  if (e.target.type !== 'checkbox') return;
  const on = [...panelDate.querySelectorAll('input:checked')].map(i => i.value);
  S.dates = dates.filter(d => on.includes(d));
  refresh();
});

// ---- columns hide / show ----
panelCols.addEventListener('change', e => {
  const k = e.target.dataset.hk; if (!k) return;
  if (e.target.checked) delete S.hidden[k]; else S.hidden[k] = true;
  save(); refresh();
});
panelCols.addEventListener('click', e => {
  const b = e.target.closest('button[data-a]'); if (!b) return;
  if (b.dataset.a === 'showall') S.hidden = {}; else S.widths = {};
  save(); buildColPanel(); refresh();
});
head.addEventListener('click', e => {
  const x = e.target.closest('.x'); if (!x) return;
  S.hidden[x.dataset.hide] = true;
  save(); buildColPanel(); refresh();
});

// ---- column chhota/bada (header ki kinaare se kheencho) ----
let drag = null;
head.addEventListener('pointerdown', e => {
  const h = e.target.closest('.rs'); if (!h) return;
  drag = { key: h.dataset.w, startX: e.clientX, startW: h.parentElement.getBoundingClientRect().width };
  h.setPointerCapture(e.pointerId); e.preventDefault();
});
head.addEventListener('pointermove', e => {
  if (!drag) return;
  S.widths[drag.key] = Math.max(40, Math.round(drag.startW + e.clientX - drag.startX));
  applyWidths();
});
head.addEventListener('pointerup', () => { if (drag) { drag = null; save(); } });

// ---- dropdown open/close ----
document.querySelectorAll('.dd > button').forEach(b => b.addEventListener('click', () => {
  const dd = b.parentElement, was = dd.classList.contains('open');
  document.querySelectorAll('.dd').forEach(x => x.classList.remove('open'));
  if (!was) dd.classList.add('open');
}));
document.querySelectorAll('.dd').forEach(dd => dd.addEventListener('click', e => e.stopPropagation()));
document.addEventListener('click', () => document.querySelectorAll('.dd').forEach(x => x.classList.remove('open')));

q.oninput = renderBody;
buildDatePanel(); buildColPanel(); refresh();
</script></body></html>
"""


TOP10_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TOP RATED - MYNTRA</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;height:100vh;display:flex;flex-direction:column;font-family:"Segoe UI",Arial,sans-serif;background:#f4f5f7;color:#222}
  .bar{background:#fff;border-bottom:1px solid #ddd;padding:10px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .bar h1{font-size:18px;margin:0 8px 0 0}
  .bar a.nav{padding:7px 12px;border:1px solid #bbb;border-radius:6px;color:#1d4ed8;text-decoration:none;font-size:14px;background:#fff}
  .bar a.nav:hover{background:#eff6ff}
  .bar label{font-size:13.5px;color:#444;display:flex;align-items:center;gap:6px}
  .bar input,.bar select{padding:6px 8px;font-size:14px;border:1px solid #bbb;border-radius:6px;background:#fff}
  .bar input{width:70px}
  .note{color:#666;font-size:12.5px}
  .wrap{flex:1;overflow:auto}
  table{border-collapse:separate;border-spacing:0;background:#fff;min-width:100%}
  th{position:sticky;top:0;z-index:3;background:#1f2937;color:#fff;padding:9px 10px;font-size:12.5px;text-align:left;white-space:nowrap;border-right:1px solid #374151}
  th.day{text-align:center;min-width:104px}
  td{padding:6px 10px;border-bottom:1px solid #eee;font-size:13.5px;vertical-align:middle;white-space:nowrap}
  td.day{text-align:center;min-width:104px}
  td.day b{display:block;font-size:14px}
  td.day small{display:block;color:#666;font-size:11.5px}
  td.up b{color:#15803d}
  td.down b{color:#b91c1c}
  td.mut{color:#9ca3af}
  td.rank{font-weight:700;font-size:16px;text-align:center;color:#92400e}
  td.price{font-weight:700}
  td.chg{font-size:12.5px;line-height:1.5}
  .pos{color:#15803d;font-weight:600}
  .neg{color:#b91c1c;font-weight:600}
  .zero{color:#6b7280}
  tr:hover td{background:#eef6ff}
  td img{height:72px;width:auto;border-radius:4px;cursor:zoom-in;display:block}
  td a{color:#1d4ed8;text-decoration:none}
  td a:hover{text-decoration:underline}
  td.empty{padding:30px;color:#666;white-space:normal}
</style></head>
<body>
<div class="bar">
  <h1>TOP RATED PRODUCTS</h1>
  <a class="nav" href="__MAIN_LINK__">&larr; Main sheet</a>
  <label>Show top <select id="topn"><option>10</option><option>20</option><option>50</option></select></label>
  <label>Min reviews <input id="minrev" type="number" min="0" value="10"></label>
  <span class="note" id="note"></span>
</div>
<div class="wrap"><table><thead id="head"></thead><tbody id="body"></tbody></table></div>
<script>
const DATA = __DATA__;
const H = DATA.headers, R = DATA.rows, PHOTO = DATA.photoCol;
const DATE_RE = /^(\d{2})\/(\d{2})\/(\d{4})/;
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const $ = id => document.getElementById(id);
const pad = n => String(n).padStart(2, '0');
const fmtDate = d => pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear();
const dkey = s => s.slice(6, 10) + s.slice(3, 5) + s.slice(0, 2);

// date -> { PRICE:colIndex, STAR:colIndex, REVIEWS:colIndex, ... }
const byDate = {};
H.forEach((h, i) => {
  const m = h.match(DATE_RE); if (!m) return;
  const rest = h.slice(m[0].length).trim() || 'PRICE';
  (byDate[m[0]] = byDate[m[0]] || {})[rest] = i;
});
const dates = Object.keys(byDate).sort((a, b) => dkey(a) < dkey(b) ? -1 : 1);

const num = s => { const v = parseFloat(String(s == null ? '' : s).replace(/[^0-9.]/g, '')); return isNaN(v) ? null : v; };
const toInt = s => { const v = parseInt(String(s == null ? '' : s).replace(/[^0-9]/g, ''), 10); return isNaN(v) ? null : v; };

let days = [];
if (dates.length) {
  const L = dates[dates.length - 1];
  const base = new Date(+L.slice(6, 10), +L.slice(3, 5) - 1, +L.slice(0, 2));
  for (let k = 6; k >= 0; k--) days.push(fmtDate(new Date(base.getFullYear(), base.getMonth(), base.getDate() - k)));
}

const prods = R.map(r => {
  const hist = days.map(d => {
    const c = byDate[d]; if (!c) return null;
    return { star: c.STAR != null ? num(r[c.STAR]) : null,
             rev: c.REVIEWS != null ? toInt(r[c.REVIEWS]) : null,
             price: c.PRICE != null ? r[c.PRICE] : '' };
  });
  let cur = null;
  for (let k = hist.length - 1; k >= 0; k--) { if (hist[k] && hist[k].star != null) { cur = hist[k]; break; } }
  let first = null;
  for (let k = 0; k < hist.length; k++) { if (hist[k] && hist[k].star != null) { first = hist[k]; break; } }
  const link = (DATA.linkCol >= 0 && /^https?:\/\//i.test(r[DATA.linkCol] || '')) ? r[DATA.linkCol] : 'https://www.myntra.com/' + r[DATA.idCol];
  return { id: r[DATA.idCol], photo: r[PHOTO], link, hist, cur, first, price: cur ? cur.price : '' };
});

function dayCell(h, prev) {
  if (!h || h.star == null) return '<td class="day mut">&ndash;</td>';
  let cls = '';
  if (prev && prev.star != null) cls = h.star > prev.star ? ' up' : (h.star < prev.star ? ' down' : '');
  return '<td class="day' + cls + '"><b>&#9733; ' + h.star.toFixed(1) + '</b><small>' + (h.rev != null ? h.rev + ' reviews' : '') + '</small></td>';
}
function signed(v, digits) {
  const t = v.toFixed(digits);
  const cls = v > 0 ? 'pos' : (v < 0 ? 'neg' : 'zero');
  return '<span class="' + cls + '">' + (v > 0 ? '+' : '') + t + '</span>';
}

function render() {
  const topn = +$('topn').value, minrev = +$('minrev').value || 0;
  if (!days.length) {
    $('head').innerHTML = '';
    $('body').innerHTML = '<tr><td class="empty">Abhi koi date wala data nahi hai.</td></tr>';
    return;
  }
  $('head').innerHTML = '<tr><th>#</th><th>PHOTO</th><th>STYLE ID</th><th>PRICE</th>' +
    days.map(d => '<th class="day">' + d + '</th>').join('') + '<th>7 DAY CHANGE</th></tr>';
  const list = prods.filter(p => p.cur && p.cur.star > 0 && (p.cur.rev || 0) >= minrev)
    .sort((a, b) => (b.cur.star - a.cur.star) || ((b.cur.rev || 0) - (a.cur.rev || 0)) || (a.id < b.id ? -1 : 1))
    .slice(0, topn);
  $('note').textContent = 'Latest: ' + dates[dates.length - 1] + ' | ' + days[0] + ' se ' + days[6] +
    ' tak ki history | star ke hisaab se, barabar hone par zyada reviews wala upar';
  if (!list.length) {
    $('body').innerHTML = '<tr><td class="empty">Is "Min reviews" ke saath koi product nahi mila. Min reviews kam karke dekho.</td></tr>';
    return;
  }
  $('body').innerHTML = list.map((p, n) => {
    let prev = null;
    const cells = p.hist.map(h => {
      const c = dayCell(h, prev);
      if (h && h.star != null) prev = h;
      return c;
    }).join('');
    const dStar = p.cur.star - (p.first ? p.first.star : p.cur.star);
    const dRev = (p.cur.rev || 0) - (p.first ? (p.first.rev || 0) : (p.cur.rev || 0));
    return '<tr><td class="rank">' + (n + 1) + '</td>' +
      '<td>' + (p.photo ? '<img src="' + esc(p.photo) + '" loading="lazy" onclick="window.open(this.src)">' : '') + '</td>' +
      '<td><a href="' + esc(p.link) + '" target="_blank" rel="noopener">' + esc(p.id) + '</a></td>' +
      '<td class="price">' + esc(p.price) + '</td>' + cells +
      '<td class="chg">Star: ' + signed(dStar, 1) + '<br>Reviews: ' + signed(dRev, 0) + '</td></tr>';
  }).join('');
}
$('topn').onchange = render; $('minrev').oninput = render;
render();
</script></body></html>
"""


def fmt_cell(v, header):
    if v is None:
        return ""
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (int, float)):
        if re.match(r"^\d{2}/\d{2}/\d{4}$", header):
            return f"₹{int(v):,}"
        if header.endswith("STAR"):
            return f"{v:.1f}"
        return str(v)
    return str(v)


def build_html(ws, last_row, sid_col, photo_col):
    ncols = ws.max_column
    while ncols > photo_col and cell_empty(ws.cell(1, ncols).value):
        ncols -= 1
    headers = [header_text(ws.cell(1, c).value) for c in range(1, ncols + 1)]
    rows = []
    for r in range(2, last_row + 1):
        row = [fmt_cell(ws.cell(r, c).value, headers[c - 1]) for c in range(1, ncols + 1)]
        sid = sid_of(ws.cell(r, sid_col).value)
        has_photo = bool(sid) and os.path.exists(os.path.join(PHOTO_DIR, f"{sid}.jpg"))
        row[photo_col - 1] = f"{PHOTO_DIR}/{sid}.jpg" if has_photo else ""
        rows.append(row)
    # product ka link kis column me hai (jis column me http se shuru hone wali values hon)
    link_col = -1
    for c in range(ncols):
        if c == photo_col - 1:
            continue
        if any(r[c].strip().lower().startswith("http") for r in rows[:50]):
            link_col = c
            break
    payload = json.dumps({"headers": headers, "rows": rows, "photoCol": photo_col - 1,
                          "idCol": sid_col - 1, "linkCol": link_col},
                         ensure_ascii=False).replace("</", "<\\/")
    main_link, top_link = quote(HTML_FILE), quote(TOP10_FILE)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(HTML_TEMPLATE.replace("__TOP_LINK__", top_link).replace("__DATA__", payload))
    with open(TOP10_FILE, "w", encoding="utf-8") as f:
        f.write(TOP10_TEMPLATE.replace("__MAIN_LINK__", main_link).replace("__DATA__", payload))


def main():
    wb = load_book()
    ws = wb.active

    # Header me agar Excel ne date bana di hai to wapas text bana do
    for c in range(1, ws.max_column + 1):
        v = ws.cell(1, c).value
        if isinstance(v, (datetime.datetime, datetime.date)):
            set_text(ws.cell(1, c), header_text(v))

    # Agar 2 PHOTO column ban gaye hain to extra wala hata do
    repair_duplicate_photo(ws)

    def find_col(name):
        for c in range(1, ws.max_column + 1):
            if header_text(ws.cell(1, c).value).upper() == name:
                return c
        return None

    # Columns header ke naam se dhundo (TYPE jaise naye column aane se STYLE ID ki jagah badal sakti hai)
    sid_col = find_col("STYLE ID") or 1
    photo_col = find_col("PHOTO")
    if photo_col is None:
        # PHOTO column nahi hai: pehli date wale column se theek pehle naya column daalo
        photo_col = ws.max_column + 1
        for c in range(1, ws.max_column + 1):
            if DATE_HDR.match(header_text(ws.cell(1, c).value)):
                photo_col = c
                break
        ws.insert_cols(photo_col)
        set_text(ws.cell(1, photo_col), "PHOTO")

    last_row = ws.max_row
    while last_row > 1 and cell_empty(ws.cell(last_row, sid_col).value):
        last_row -= 1
    if last_row < 2:
        print("Sheet me data nahi mila.")
        return

    today = datetime.date.today().strftime("%d/%m/%Y")

    # D me purana =IMAGE formula ya link ho to uska link yaad rakh ke cell saaf kar do
    photo_links = {}
    for r in range(2, last_row + 1):
        v = ws.cell(r, photo_col).value
        if isinstance(v, str):
            m = re.search(r'IMAGE\("([^"]+)"', v)
            if m:
                photo_links[r] = m.group(1)
                ws.cell(r, photo_col).value = None
            elif v.strip().lower().startswith("http"):
                photo_links[r] = v.strip()
                ws.cell(r, photo_col).value = None

    row_ids = {}
    for r in range(2, last_row + 1):
        sid = sid_of(ws.cell(r, sid_col).value)
        if sid:
            row_ids[r] = sid

    img_rows = image_rows(ws, photo_col)

    # Kaunse columns me likhna hai: pehle aaj ki date wala block, nahi to pehle 5 khali columns (E se)
    def col_empty(c):
        return all(cell_empty(ws.cell(r, c).value) for r in range(2, last_row + 1))

    first_data_col = photo_col + 1
    target = None
    for c in range(first_data_col, ws.max_column + 1):
        if header_text(ws.cell(1, c).value) == today:
            target = c
            break
    if target is None:
        c = first_data_col
        while True:
            if all(col_empty(c + k) for k in range(BLOCK)):
                target = c
                break
            c += 1

    # Kis product ko photo chahiye
    need_image = {}
    for r, sid in row_ids.items():
        need_image[sid] = need_image.get(sid, False) or (r not in img_rows)

    todo = list(dict.fromkeys(row_ids.values()))  # duplicate style IDs ek hi baar
    print(f"{len(todo)} products fetch ho rahe hain...")

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        flags = [need_image[s] for s in todo]
        for n, (sid, info) in enumerate(zip(todo, ex.map(get_info, todo, flags)), 1):
            results[sid] = info
            if n % 20 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} done")

    # ---- Listing page par rank dhundo (TYPE ke hisaab se link, aur KALINI brand filter ke saath bhi) ----
    type_col = None
    for c in range(1, ws.max_column + 1):
        if header_text(ws.cell(1, c).value).upper() == "TYPE":
            type_col = c
            break
    if type_col is None:
        print("'TYPE' naam ka column nahi mila - rank (page/position) nahi nikala jayega.")

    row_type = {}
    for r, sid in row_ids.items():
        if type_col and sid not in row_type:
            row_type[sid] = type_key(ws.cell(r, type_col).value)

    skip_note, groups = {}, {}
    for sid in todo:
        if str(results[sid][4]).startswith("ALL"):
            skip_note[sid] = "Stock out"              # saare size stock out -> rank nahi dhundna
        elif not row_type.get(sid):
            skip_note[sid] = "TYPE nahi mila"
        else:
            groups.setdefault(row_type[sid], []).append(sid)

    ranks, branks, scan_info = {}, {}, {}
    print(f"Rank dhundh raha hoon... ({len(skip_note)} products skip: stock out / TYPE nahi)")
    for ty, sids in groups.items():
        rk, sc, ok = find_ranks(LISTINGS[ty], sids, MAX_PAGES)
        brk, bsc, bok = find_ranks(LISTINGS[ty], sids, MAX_BRAND_PAGES, brand=BRAND)
        ranks.update(rk)
        branks.update(brk)
        scan_info[ty] = (sc, ok, bsc, bok)
        print(f"  {ty}: {len(rk)}/{len(sids)} mile ({sc} page) | {BRAND} filter me {len(brk)}/{len(sids)} mile ({bsc} page)")
        if not ok:
            print(f"  {ty}: listing ka pehla page nahi khula - Myntra ne roka ya LISTINGS ka link galat hai.")

    # ---- Aaj ka data Excel me likho ----
    heads = [today, f"{today} STAR", f"{today} REVIEWS", f"{today} OUT OF STOCK", f"{today} SELLERS",
             f"{today} PAGE", f"{today} POSITION", f"{today} BRAND PAGE", f"{today} BRAND POSITION"]
    for k, h in enumerate(heads):
        set_text(ws.cell(1, target + k), h)

    for r, sid in row_ids.items():
        price, star, rev, _image, oos, sellers = results[sid]
        set_value(ws.cell(r, target), price)
        set_value(ws.cell(r, target + 1), star)
        set_value(ws.cell(r, target + 2), rev)
        ws.cell(r, target + 3).value = oos
        ws.cell(r, target + 4).value = sellers
        sc, ok, bsc, bok = scan_info.get(row_type.get(sid), (0, True, 0, True))
        if sid in skip_note:
            vals = [skip_note[sid], "", skip_note[sid], ""]
        else:
            vals = list(ranks[sid]) if sid in ranks else [miss_text(sc, MAX_PAGES, ok), ""]
            vals += list(branks[sid]) if sid in branks else [miss_text(bsc, MAX_BRAND_PAGES, bok), ""]
        for k, v in enumerate(vals):
            ws.cell(r, target + 5 + k).value = v

    # ---- Photos (sirf jin rows me nahi hai) ----
    os.makedirs(PHOTO_DIR, exist_ok=True)
    need_rows = [(r, sid) for r, sid in row_ids.items() if r not in img_rows]
    url_for = {}
    for r, sid in need_rows:
        u = photo_links.get(r) or results[sid][3]
        if u:
            url_for.setdefault(sid, u)
    sids_need = list(dict.fromkeys(sid for _, sid in need_rows))
    print(f"{len(sids_need)} products ki photo chahiye (baaki me pehle se hai)...")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        dl = list(ex.map(download_photo, [(s, url_for.get(s)) for s in sids_need]))
    paths = {sid: p for sid, p, err in dl if p}
    fails = [(sid, err) for sid, p, err in dl if not p]

    photos_added = 0
    row_h = IMG_BOX * 0.75 + 3  # pixels -> points
    for r, sid in need_rows:
        p = paths.get(sid)
        if not p:
            continue
        img = XLImage(p)
        scale = IMG_BOX / max(img.width, img.height)
        img.width = int(img.width * scale)
        img.height = int(img.height * scale)
        ws.add_image(img, f"{get_column_letter(photo_col)}{r}")
        ws.row_dimensions[r].height = row_h
        photos_added += 1
    for r in img_rows:
        if ws.row_dimensions[r].height is None or ws.row_dimensions[r].height < row_h:
            ws.row_dimensions[r].height = row_h

    # ---- Dikhne me achha banao ----
    center = Alignment(vertical="center")
    last_col = target + BLOCK - 1
    for row in ws.iter_rows(min_row=1, max_row=last_row, max_col=last_col):
        for cell in row:
            cell.alignment = center
    ws.column_dimensions[get_column_letter(photo_col)].width = IMG_BOX / 7 + 1
    for k in range(BLOCK):
        col = target + k
        longest = max((len(str(ws.cell(r, col).value or "")) for r in range(1, min(last_row, 300) + 1)), default=10)
        ws.column_dimensions[get_column_letter(col)].width = min(max(longest, 10) + 2, 45)
    ws.freeze_panes = f"{get_column_letter(first_data_col)}2"

    # ---- Backup banake save ----
    if os.path.exists(FILE):
        shutil.copyfile(FILE, FILE.replace(".xlsx", "_backup.xlsx"))
    try:
        wb.save(FILE)
    except PermissionError:
        print(f"'{FILE}' save nahi ho payi. Excel me khuli hai to usse band karke dobara chalao.")
        return

    try:
        build_html(ws, last_row, sid_col, photo_col)
        print(f"Chrome me dekhne wali files bani: {HTML_FILE} aur {TOP10_FILE}")
    except Exception as e:  # HTML na bane to bhi Excel file save ho chuki hai
        print(f"Chrome wali HTML file nahi ban payi: {e}")

    errors = sum(1 for v in results.values() if v[0].startswith("Error"))
    no_rating = sum(1 for v in results.values() if v[1] == "N/A")
    no_stock = sum(1 for v in results.values() if v[4] == "N/A")
    print(f"Ho gaya! Columns {get_column_letter(target)}-{get_column_letter(last_col)} me {today} ka data add ho gaya.")
    print(f"Nayi photos: {photos_added}/{len(need_rows)} | Price errors: {errors}/{len(todo)} | "
          f"Rating nahi mili: {no_rating}/{len(todo)} | Size/seller nahi mila: {no_stock}/{len(todo)} | "
          f"Rank mila: {len(ranks)}/{len(todo)}")
    for sid, err in fails[:3]:
        print(f"  Photo fail: {sid} -> {err} | {url_for.get(sid, '')}")
    if todo and errors == len(todo):
        print("Saare products me error aaya - Myntra is computer ko bhi block kar raha hai.")


if __name__ == "__main__":
    main()
