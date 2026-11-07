import os
import requests
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

def fetch_myntra_image(product_url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
        response = requests.get(product_url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            meta_img = soup.find("meta", property="og:image")
            if meta_img and meta_img.get("content"):
                return meta_img["content"]
    except Exception as e:
        print(f"Error fetching image for {product_url}: {e}")
    return ""

def main():
    print("Google Sheet se connection ban raha hai...")
    client = get_google_sheet_client()
    sheet = client.open(SHEET_NAME).sheet1
    
    all_rows = sheet.get_all_values()
    if not all_rows or len(all_rows) < 2:
        print("Sheet me data nahi mila!")
        return

    headers = [str(h).strip().upper() for h in all_rows[0]]
    
    # Pehla PHOTO column (Column C / index 3) target karna
    photo_col_idx = -1
    for idx, h in enumerate(headers):
        if h == "PHOTO":
            photo_col_idx = idx + 1
            break
            
    if photo_col_idx == -1:
        print("PHOTO column nahi mila!")
        return

    # STYLE ID LINK column dhundna
    link_col_idx = -1
    for idx, h in enumerate(headers):
        if "LINK" in h or "STYLE ID" in h:
            link_col_idx = idx + 1
            break

    if link_col_idx == -1:
        print("Style ID Link column nahi mila!")
        return

    print("Products check ho rahe hain aur missing photos update ki ja rahi hain...")
    
    for idx, row in enumerate(all_rows[1:], start=2):
        current_photo = row[photo_col_idx - 1].strip() if len(row) >= photo_col_idx else ""
        
        if not current_photo or current_photo.lower() == "nan" or current_photo == "":
            product_link = row[link_col_idx - 1].strip() if len(row) >= link_col_idx else ""
            
            if product_link and not product_link.startswith("http"):
                product_link = f"https://www.myntra.com/{product_link}"
            
            if product_link and product_link.startswith("http"):
                print(f"Fetching photo for row {idx} -> {product_link}")
                img_url = fetch_myntra_image(product_link)
                if img_url:
                    sheet.update_cell(idx, photo_col_idx, img_url)
                    print(f"Updated photo for row {idx}")

    print("Tracker script successfully run ho gayi hai!")

if __name__ == "__main__":
    main()
