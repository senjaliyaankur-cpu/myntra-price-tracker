"""
Roz ka update (command line se):

    python myntra_excel_tracker.py          # sirf wahi products jinka aaj ka data nahi aaya
    python myntra_excel_tracker.py --force  # sabhi products dobara

Google Sheet ("MYNTRA PRICE") me aaj ki date ke 5 columns (price, star, reviews, out of stock, sellers)
apne aap bana kar bharta hai, aur khali / galat photo ka sahi Myntra image URL bhi daalta hai.

Ye GitHub Actions (.github/workflows/update.yml) se roz apne aap chalta hai. Manual chalane ke liye:
GitHub repo > Actions > "Update Myntra data" > Run workflow.

GitHub secrets (Settings > Secrets and variables > Actions):
  GCP_SECRETS_TOML : Streamlit app ke Secrets ka poora text (jisme [gcp_service_account] hai)
  MYNTRA_PROXY     : (optional) India proxy, agar Myntra GitHub ko block kare:  http://user:pass@host:port
"""

import sys

from myntra_core import open_sheet, update_today


def main():
    force = "--force" in sys.argv
    print("Google Sheet se connection ban raha hai...")
    try:
        sheet = open_sheet()
    except Exception as e:
        print(f"Google Sheet khul nahi payi: {e}")
        sys.exit(1)

    def progress(done, total):
        if done % 10 == 0 or done == total:
            print(f"  {done}/{total} products fetch hue")

    r = update_today(sheet, progress=progress, force=force)
    print(f"Ho gaya! Date: {r['date']} | Products: {r['products']} | Fetch kiye: {r['fetched']} | "
          f"Nayi photos: {r['photos']} | Data nahi mila: {r['errors']}")
    for e in r["error_ids"]:
        print("  data nahi mila:", e)
    if r["fetched"] and r["errors"] == r["fetched"]:
        print("Sab products me error aaya - Myntra is server ko block kar raha hai (403 = block).")
        print("Fix: GitHub secret MYNTRA_PROXY me koi India proxy daalo (http://user:pass@host:port).")
        sys.exit(1)


if __name__ == "__main__":
    main()
