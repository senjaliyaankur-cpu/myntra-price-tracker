"""
Roz ka update (command line se):

    python myntra_excel_tracker.py          # sirf wahi products jinka aaj ka data nahi aaya
    python myntra_excel_tracker.py --force  # sabhi products dobara

Google Sheet ("MYNTRA PRICE") me aaj ki date ke 5 columns (price, star, reviews, out of stock, sellers)
apne aap bana kar bharta hai, aur khali / galat photo ka sahi Myntra image URL bhi daalta hai.

Credentials: is folder me credentials.json rakho (ya GCP_SERVICE_ACCOUNT environment variable).
Myntra kabhi kabhi cloud servers ko block karta hai, isliye ye script apne computer par chalana sabse pakka hai.
"""

import sys

from myntra_core import open_sheet, update_today


def main():
    force = "--force" in sys.argv
    print("Google Sheet se connection ban raha hai...")
    sheet = open_sheet()

    def progress(done, total):
        if done % 10 == 0 or done == total:
            print(f"  {done}/{total} products fetch hue")

    r = update_today(sheet, progress=progress, force=force)
    print(f"Ho gaya! Date: {r['date']} | Products: {r['products']} | Fetch kiye: {r['fetched']} | "
          f"Nayi photos: {r['photos']} | Data nahi mila: {r['errors']}")
    for e in r["error_ids"]:
        print("  data nahi mila:", e)
    if r["fetched"] and r["errors"] == r["fetched"]:
        print("Sab products me error aaya - Myntra is computer/server ko block kar raha ho sakta hai.")


if __name__ == "__main__":
    main()
