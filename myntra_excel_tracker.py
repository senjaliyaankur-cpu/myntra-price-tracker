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

def fetch_real_myntra_image(product_url):
    """Myntra page source se window.__myx JSON parse karke exact real image URL extract karta hai."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        res = requests.get(product_url, headers=headers, timeout=10)
        if res.status_code == 200:
            # 1. Approach: window.__myx JSON state search karna
            match = re.search(r'window\.__myx\s*=\s*(\{.*?\});?</script>', res.text)
            if match:
                data = json.loads(match.group(1))
                media_assets = data.get("pdpData", {}).get("media", {}).get("albums", [])
                if media_assets:
                    for album in media_assets:
                        images = album.get("images", [])
                        if images and "src" in images[0]:
                            return images[0]["src"]
            
            # 2. Fallback: Direct assets.myntassets.com pattern regex search
            img_matches = re.findall(r'https://assets\.myntassets\.com/[^\s"\'<>]+?\.(?:jpg|jpeg|png)', res.text)
            if img_matches:
                # Valid high-res product photo choose karna
                for img_url in img_matches:
                    if "/assets/images/" in img_url and "default" not in img_url:
                        return img_url
                        
    except Exception as e:
        print(f"Error scraping image for {product_url}: {e}")
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
    
    # Pehla PHOTO column index (1-based)
    photo_col_idx = -1
    for idx, h in enumerate(headers):
        if h == "PHOTO":
            photo_col_idx = idx + 1
            break
            
    if photo_col_idx == -1:
        print("PHOTO column nahi mila!")
        return

    # STYLE ID LINK ya STYLE ID column
    link_col_idx = -1
    for idx, h in enumerate(headers):
        if "LINK" in h or "STYLE ID" in h:
            link_col_idx = idx + 1
            break

    if link_col_idx == -1:
        print("Style ID Link column nahi mila!")
        return

    print("Products check ho rahe hain aur Real Image URLs fetch kiye ja rahe hain...")
    
    photo_updates = []
    
    for idx, row in enumerate(all_rows[1:], start=2):
        current_photo = row[photo_col_idx - 1].strip() if len(row) >= photo_col_idx else ""
        
        # Sirf khali/invalid cells ko update karega (jo aapne pehle se add kiye hain unko waisa hi rakhega)
        if not current_photo or current_photo.lower() == "nan" or "default/image.jpg" in current_photo:
            product_link = row[link_col_idx - 1].strip() if len(row) >= link_col_idx else ""
            
            if product_link and not product_link.startswith("http"):
                product_link = f"https://www.myntra.com/{product_link}"
            
            if product_link and product_link.startswith("http"):
                print(f"Row {idx}: Scraping real image URL -> {product_link}")
                real_img_url = fetch_real_myntra_image(product_link)
                
                if real_img_url:
                    photo_updates.append({"row": idx, "col": photo_col_idx, "val": real_img_url})
                    print(f"Found: {real_img_url}")

    if photo_updates:
        print(f"Total {len(photo_updates)} real photo URLs mil gaye hain. Batch update starting...")
        cell_list = [gspread.Cell(item["row"], item["col"], item["val"]) for item in photo_updates]
        sheet.update_cells(cell_list)
        print("Google Sheet me saari real photos update ho gayi hain!")
    else:
        print("Koi naye image links update karne ki zaroorat nahi thi.")

if __name__ == "__main__":
    main()
