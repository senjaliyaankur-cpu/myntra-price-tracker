import os
import requests
import json
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = "MYNTRA PRICE"

def get_google_sheet_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    if os.path.exists("credentials.json"):
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    else:
        import streamlit as st
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def fetch_real_myntra_image(style_id):
    """Myntra product page se exact real hash-code high-res image URL extract karta hai."""
    url = f"https://www.myntra.com/{style_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            # 1. window.__myx JSON state se real image dhundna
            match = re.search(r'window\.__myx\s*=\s*(\{.*?\});?</script>', res.text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    albums = data.get("pdpData", {}).get("media", {}).get("albums", [])
                    for album in albums:
                        images = album.get("images", [])
                        if images and "src" in images[0]:
                            src_url = images[0]["src"]
                            if "assets.myntassets.com" in src_url:
                                return src_url
                except Exception:
                    pass
            
            # 2. Fallback: page ke andar se exact hash-code asset link match karna
            img_matches = re.findall(r'https://assets\.myntassets\.com/[^\s"\'<>]+?\.(?:jpg|jpeg|png)', res.text)
            for img_url in img_matches:
                if "/assets/images/" in img_url and "default" not in img_url and "search" not in img_url:
                    return img_url
    except Exception as e:
        print(f"Error fetching image for style {style_id}: {e}")
    
    # Agar kisi wajah se page block ho toh fallback standard URL
    return f"https://assets.myntassets.com/h_720,q_90,w_540/v1/assets/images/{style_id}/default/image.jpg"

def main():
    print("Google Sheet se connection ban raha hai...")
    client = get_google_sheet_client()
    sheet = client.open(SHEET_NAME).sheet1
    
    all_rows = sheet.get_all_values()
    if not all_rows or len(all_rows) < 2:
        print("Sheet me data nahi mila!")
        return

    headers = [str(h).strip().upper() for h in all_rows[0]]
    
    photo_col_idx = -1
    for idx, h in enumerate(headers):
        if h == "PHOTO" or h.startswith("PHOTO_"):
            photo_col_idx = idx + 1
            break
            
    if photo_col_idx == -1:
        print("PHOTO column nahi mila!")
        return

    link_col_idx = -1
    for idx, h in enumerate(headers):
        if "LINK" in h or "STYLE ID" in h:
            link_col_idx = idx + 1
            break

    if link_col_idx == -1:
        print("Style ID Link column nahi mila!")
        return

    print("Products ke real image URLs check aur fetch kiye ja rahe hain...")
    
    photo_updates = []
    
    for idx, row in enumerate(all_rows[1:], start=2):
        current_photo = row[photo_col_idx - 1].strip() if len(row) >= photo_col_idx else ""
        
        # Agar photo cell khali hai ya default link pada hai
        if not current_photo or current_photo.lower() == "nan" or current_photo == "" or "default/image.jpg" in current_photo:
            product_link = row[link_col_idx - 1].strip() if len(row) >= link_col_idx else ""
            
            numbers = re.findall(r'\d+', product_link)
            if numbers:
                style_id = numbers[-1]
                print(f"Fetching real image for Style {style_id}...")
                img_url = fetch_real_myntra_image(style_id)
                if img_url:
                    photo_updates.append({"row": idx, "col": photo_col_idx, "val": img_url})
                    print(f"-> Found: {img_url}")

    if photo_updates:
        print(f"Total {len(photo_updates)} real images update ho rahi hain...")
        cell_list = [gspread.Cell(item["row"], item["col"], item["val"]) for item in photo_updates]
        sheet.update_cells(cell_list)
        print("Saari real images Google Sheet me successfully update ho gayi hain!")
    else:
        print("Sabhi rows me pehle se valid images maujood hain.")

if __name__ == "__main__":
    main()
