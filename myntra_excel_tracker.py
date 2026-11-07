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
        raise Exception("credentials.json file nahi mili!")
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
    
    photo_col_idx = -1
    for idx, h in enumerate(headers):
        if h == "PHOTO":
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

    print("Products process ho rahe hain...")
    
    # Batch update list
    photo_updates = []
    
    for idx, row in enumerate(all_rows[1:], start=2):
        current_photo = row[photo_col_idx - 1].strip() if len(row) >= photo_col_idx else ""
        
        if not current_photo or current_photo.lower() == "nan" or current_photo == "":
            product_link = row[link_col_idx - 1].strip() if len(row) >= link_col_idx else ""
            
            numbers = re.findall(r'\d+', product_link)
            if numbers:
                style_id = numbers[-1]
                img_url = f"https://assets.myntassets.com/h_720,q_90,w_540/v1/assets/images/{style_id}/default/image.jpg"
                photo_updates.append({"row": idx, "col": photo_col_idx, "val": img_url})

    if photo_updates:
        print(f"Total {len(photo_updates)} photos update karni hain. Batch write start ho raha hai...")
        # Single cell update ki jagah batch update (1 single API call)
        cell_list = []
        for item in photo_updates:
            cell_list.append(gspread.Cell(item["row"], item["col"], item["val"]))
            
        sheet.update_cells(cell_list)
        print("Saari photos ek saath Google Sheet me update ho gayi hain!")
    else:
        print("Koi naye photo links update karne ki zaroorat nahi thi.")

if __name__ == "__main__":
    main()
