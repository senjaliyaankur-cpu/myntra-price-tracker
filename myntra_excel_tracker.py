import os
import re
import json
import requests
import gspread

from bs4 import BeautifulSoup
from urllib.parse import urlparse
from oauth2client.service_account import ServiceAccountCredentials


SHEET_NAME = "MYNTRA PRICE"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
}


def get_google_sheet_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    if os.path.exists("credentials.json"):
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            "credentials.json", scope
        )
    else:
        import streamlit as st

        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict, scope
        )

    return gspread.authorize(creds)


def get_style_id(product_link):
    """Product URL ya direct Style ID se numeric ID nikale."""

    value = str(product_link).strip()

    if not value:
        return None

    # Direct numeric Style ID
    if value.isdigit():
        return value

    # URL ke path segments mein numeric ID dhundo
    try:
        path_parts = urlparse(value).path.strip("/").split("/")

        for part in reversed(path_parts):
            if part.isdigit():
                return part

    except Exception:
        pass

    # Myntra URL ke liye fallback
    match = re.search(r"/(\d{5,})(?:[/?#]|$)", value)

    if match:
        return match.group(1)

    return None


def normalize_image_url(url):
    if not isinstance(url, str):
        return None

    url = url.strip()

    if url.startswith("//"):
        url = "https:" + url

    if url.startswith("http://"):
        url = "https://" + url[7:]

    if url.startswith("https://"):
        return url

    return None


def find_first_image(data):
    """
    API JSON mein primary image ko priority se dhundhta hai.
    Gallery ki pehli image hi return karta hai.
    """

    if isinstance(data, dict):

        # Myntra search image ko priority
        search_image = data.get("searchImage")

        if isinstance(search_image, str):
            image_url = normalize_image_url(search_image)
            if image_url:
                return image_url

        # Product detail image
        views = data.get("views")

        if isinstance(views, dict):
            default_view = views.get("default")

            if isinstance(default_view, dict):
                image1 = default_view.get("image1")

                if isinstance(image1, dict):
                    image_url = normalize_image_url(
                        image1.get("secureUrl") or image1.get("url")
                    )
                    if image_url:
                        return image_url

                elif isinstance(image1, str):
                    image_url = normalize_image_url(image1)
                    if image_url:
                        return image_url

        # Common image fields
        for key in ("image", "imageUrl", "imageURL", "secureUrl"):
            value = data.get(key)

            if isinstance(value, str):
                image_url = normalize_image_url(value)
                if image_url:
                    return image_url

        # First image from image list
        for key in ("images", "imageList", "media"):
            values = data.get(key)

            if isinstance(values, list) and values:
                first = values[0]

                if isinstance(first, str):
                    image_url = normalize_image_url(first)
                    if image_url:
                        return image_url

                if isinstance(first, dict):
                    for image_key in ("secureUrl", "url", "src"):
                        image_url = normalize_image_url(
                            first.get(image_key)
                        )
                        if image_url:
                            return image_url

        # Nested API response
        for value in data.values():
            if isinstance(value, (dict, list)):
                image_url = find_first_image(value)
                if image_url:
                    return image_url

    elif isinstance(data, list):
        for item in data:
            image_url = find_first_image(item)
            if image_url:
                return image_url

    return None


def get_image_from_api(style_id):
    api_url = (
        f"https://www.myntra.com/gateway/v2/product/{style_id}"
    )

    try:
        response = requests.get(
            api_url,
            headers=HEADERS,
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            image_url = find_first_image(data)

            if image_url:
                print("API se first image mil gayi.")
                return image_url

    except Exception as e:
        print(f"API image fetch nahi hui: {e}")

    return None


def get_image_from_product_page(product_link):
    """Product page ke og:image ya JSON-LD ki first image nikale."""

    try:
        response = requests.get(
            product_link,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True
        )

        if response.status_code != 200:
            print(f"Product page status: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Open Graph primary image
        for attribute in ("property", "name"):
            tag = soup.find(
                "meta",
                attrs={attribute: "og:image"}
            )

            if tag and tag.get("content"):
                image_url = normalize_image_url(tag["content"])

                if image_url:
                    print("Product page se main image mil gayi.")
                    return image_url

        # Twitter image fallback
        tag = soup.find(
            "meta",
            attrs={"name": "twitter:image"}
        )

        if tag and tag.get("content"):
            image_url = normalize_image_url(tag["content"])

            if image_url:
                return image_url

        # JSON-LD first image
        for script in soup.find_all(
            "script",
            attrs={"type": "application/ld+json"}
        ):
            try:
                data = json.loads(script.string or script.get_text())
                image_url = find_first_image(data)

                if image_url:
                    return image_url

            except Exception:
                continue

    except Exception as e:
        print(f"Product page image fetch error: {e}")

    return None


def get_first_product_image(product_link):
    style_id = get_style_id(product_link)

    if not style_id:
        print(f"Valid Style ID nahi mila: {product_link}")
        return None

    print(f"Style ID: {style_id}")

    # Pehle Myntra API try karo
    image_url = get_image_from_api(style_id)

    if image_url:
        return image_url

    # API fail ho toh original product page try karo
    if product_link.startswith("http"):
        image_url = get_image_from_product_page(product_link)

        if image_url:
            return image_url

    print(f"First image nahi mili: {style_id}")
    return None


def main():
    print("Google Sheet se connection ban raha hai...")

    client = get_google_sheet_client()
    sheet = client.open(SHEET_NAME).sheet1

    all_rows = sheet.get_all_values()

    if not all_rows or len(all_rows) < 2:
        print("Sheet mein data nahi mila!")
        return

    headers = [
        str(header).strip().upper()
        for header in all_rows[0]
    ]

    # PHOTO column find karo
    photo_col_idx = None

    for idx, header in enumerate(headers):
        if header == "PHOTO" or header.startswith("PHOTO_"):
            photo_col_idx = idx + 1
            break

    if photo_col_idx is None:
        print("PHOTO column nahi mila!")
        return

    # Product link column find karo
    link_col_idx = None

    for idx, header in enumerate(headers):
        if (
            "LINK" in header
            or "STYLE ID" in header
        ):
            link_col_idx = idx + 1
            break

    if link_col_idx is None:
        print("LINK ya STYLE ID column nahi mila!")
        return

    photo_updates = []

    print("Products ki first images fetch ho rahi hain...")

    for row_number, row in enumerate(all_rows[1:], start=2):

        current_photo = (
            row[photo_col_idx - 1].strip()
            if len(row) >= photo_col_idx
            else ""
        )

        # Sirf blank PHOTO cells update hongi
        if current_photo and current_photo.lower() != "nan":
            continue

        product_link = (
            row[link_col_idx - 1].strip()
            if len(row) >= link_col_idx
            else ""
        )

        if not product_link:
            print(f"Row {row_number}: Product link blank hai.")
            continue

        image_url = get_first_product_image(product_link)

        if image_url:
            photo_updates.append(
                gspread.Cell(
                    row_number,
                    photo_col_idx,
                    image_url
                )
            )

            print(f"Row {row_number}: First image mil gayi.")

        else:
            print(f"Row {row_number}: Image nahi mili, cell blank rahega.")

    # Batch update
    if photo_updates:
        print(f"Total {len(photo_updates)} images update ho rahi hain...")

        sheet.update_cells(
            photo_updates,
            value_input_option="USER_ENTERED"
        )

        print("Google Sheet mein image URLs update ho gaye!")

    else:
        print("Koi nayi image update karne ke liye nahi mili.")

    print("Tracker complete!")


if __name__ == "__main__":
    main()
