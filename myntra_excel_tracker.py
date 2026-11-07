import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re

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

def main():
    print("Google Sheet se connection ban raha hai...")
    client = get_google_sheet_client()
    sheet = client.open(SHEET_NAME).sheet1
    
    all_rows = sheet.get_all_values()
    if not all_rows or len(all_rows) < 2:
        print("Sheet me data nahi mila!")
        return

    headers = [str(h).strip().upper() for h in all_rows[0]]
    
    # Pehla PHOTO column dhundna
    photo_col_idx = -1
    for idx, h in enumerate(headers):
        if h == "PHOTO":
            photo_col_idx = idx + 1
            break
            
    if photo_col_idx == -1:
        print("PHOTO column nahi mila!")
        return

    # STYLE ID LINK ya STYLE ID column dhundna
    link_col_idx = -1
    for idx, h in enumerate(headers):
        if "LINK" in h or "STYLE ID" in h:
            link_col_idx = idx + 1
            break

    if link_col_idx == -1:
        print("Style ID Link column nahi mila!")
        return

    print("Products check ho rahe hain aur direct CDN photo links generate ki ja rahi hain...")
    
    for idx, row in enumerate(all_rows[1:], start=2):
        current_photo = row[photo_col_idx - 1].strip() if len(row) >= photo_col_idx else ""
        
        if not current_photo or current_photo.lower() == "nan" or current_photo == "":
            product_link = row[link_col_idx - 1].strip() if len(row) >= link_col_idx else ""
            
            # Link ya Style ID se numbers (e.g. 40480606) nikalna
            numbers = re.findall(r'\d+', product_link)
            if numbers:
                style_id = numbers[-1] # Aakhri number style id hota hai
                # Myntra ka official standard CDN image URL pattern
                img_url = f"https://assets.myntassets.com/h_720,q_90,w_540/v1/assets/images/{style_id}/default/image.jpg"
                
                sheet.update_cell(idx, photo_col_idx, img_url)
                print(f"Updated photo for row {idx} -> Style ID: {style_id}")

    print("Tracker script successfully run ho gayi hai!")

if __name__ == "__main__":
    main()
