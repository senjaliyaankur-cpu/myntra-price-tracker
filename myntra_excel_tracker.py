import os
import requests
import re
from bs4 import BeautifulSoup
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

def get_image_for_style(style_id):
    # Myntra product page ya search se image extract karne ki koshish
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.myntra.com/"
    }
    
    # 1. Pehle direct product page try karo BeautifulSoup ke sath
    try:
        url = f"https://www.myntra.com/{style_id}"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            # Myntra image meta tag dhoondna
            meta_img = soup.find("meta", property="og:image")
            if meta_img and meta_img.get("content"):
                return meta_img["content"]
            
            # Agar meta na mile toh script tag me dhoondna
            img_match = re.search(r'https://assets\.myntassets\.com/[^\s"\'<>]+?\.(?:jpg|jpeg|png)', res.text)
            if img_match:
                found_url = img_match.group(0)
                if "assets/images/" in found_url:
                    return found_url
    except Exception:
        pass

    # 2. Agar page block ho jaye toh Myntra search API ya standard CDN link fallback
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

    print("Products ke image links check aur generate ho rahe hain...")
    
    photo_updates = []
    
    for idx, row in enumerate(all_rows[1:], start=2):
        current_photo = row[photo_col_idx - 1].strip() if len(row) >= photo_col_idx else ""
        
        # Agar photo cell khali hai ya nan hai
        if not current_photo or current_photo.lower() == "nan" or current_photo == "":
            product_link = row[link_col_idx - 1].strip() if len(row) >= link_col_idx else ""
            
            numbers = re.findall(r'\d+', product_link)
            if numbers:
                style_id = numbers[-1]
                img_url = get_image_for_style(style_id)
                if img_url:
                    photo_updates.append({"row": idx, "col": photo_col_idx, "val": img_url})
                    print(f"Row {idx} Style {style_id} -> {img_url}")

    if photo_updates:
        print(f"Total {len(photo_updates)} images update ho rahi hain...")
        cell_list = [gspread.Cell(item["row"], item["col"], item["val"]) for item in photo_updates]
        sheet.update_cells(cell_list)
        print("Saari images Google Sheet me update ho gayi hain!")
    else:
        print("Sabhi rows me pehle se images maujood hain.")

if __name__ == "__main__":
    main()
