import asyncio
import random
import time
import os
import sys
import csv
import json
from twilio.rest import Client
import pandas as pd
import easyocr

import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import dotenv
import operator
from datetime import datetime
import requests
import json
from icu import Locale,UnicodeString
import app_paths


SYSTEM_PROMPT = """You are an expert entity extraction system specialized in identifying Turkish human names in payment descriptions.
Your goal is to extract ALL human names found in the text that are DIFFERENT from the 'Sender Name'.

Rules:
1. Input will be: "Description: [text] | Sender: [name]"
2. Extract names - can be full names (First + Last) OR single names (just first name like "Ebra", "Mehmet", "Ayse").
3. Single names are VALID and should be extracted - people often write just their first name.
4. Ignore company names, bank terms (KURS, ODEME, HESAP, YAPI, KREDI, HARCI, UCRETI, EHLIYET, SINAV), and the Sender's name.
5. Output MUST be a strictly valid JSON list of strings. Example: ["Ali Yilmaz", "Ebra", "Mehmet"]
6. If no valid names are found, output empty list: []
"""
def clear_processing_status():
    status_file = app_paths.status_path()
    if os.path.exists(status_file):
        os.remove(status_file)

def update_processing_status(name, stage, payment_type=None, payment_amount=None):
    # Stages:'processing', 'almost_completed', 'completed', 'flagged'
    status = {
        "name":name,
        "stage":stage,
        "payment_type":payment_type,
        "payment_amount":payment_amount
    }
    with open(app_paths.status_path(), "w") as f:
        json.dump(status, f)

def save_payment_record(row):
    csv_path = app_paths.payments_csv_path()
    if os.path.exists(csv_path):
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)
    else:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "payment_amount", "payment_type", "status"])
            writer.writerow(row)
    clear_processing_status()
    return

def get_owed_taksit(taksit_owed):
    for row in taksit_owed:
        if "TAKSİT" in row and "ÖDEDİ" not in row:
            # row is a string like '[TAKSİT, 10.10.2025, 9.500,00, 00, ÖDEMEDİ]'
            try:
                # Remove brackets and split by comma
                cleaned_row = row.strip("[]")
                parts = [p.strip() for p in cleaned_row.split(',')]
                
                # The amount is usually at index 2: [Type, Date, Amount, ...]
                if len(parts) >= 3:
                    amount_str = parts[2]
                    # Convert "9.500,00" to float 9500.00
                    # Remove dots (thousands separator) and replace comma with dot (decimal)
                    amount_float = float(amount_str.replace('.', '').replace(',', '.'))
                    return amount_float
            except Exception as e:
                print(f"Error parsing taksit row: {row} - {e}")
                continue
    return None
tr = Locale("tr")
def turkish_pattern_check(text):
    texter = str(UnicodeString(text).toUpper(Locale("tr")))
    escaped_text = re.escape(texter)
    pattern = re.sub(r'[iİıI]', '[iİıI]', escaped_text)
    return re.compile(pattern, re.IGNORECASE)

def check_date_if_paid(date_of_payment, payments_paid):
    if len(payments_paid) == 0:
        return False
    try:
        # Handle Excel date formats (DD.MM.YYYY or YYYY-MM-DD HH:MM:SS)
        try:
            excel_date = datetime.strptime(str(date_of_payment), '%d.%m.%Y')
        except ValueError:
            try:
                # Try parsing as standard pandas timestamp string
                excel_date = datetime.strptime(str(date_of_payment), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # Try just YYYY-MM-DD
                excel_date = datetime.strptime(str(date_of_payment).split(' ')[0], '%Y-%m-%d')

        for row in payments_paid:
            # row is a string like '[Type, Date, Amount, Status]'
            try:
                if isinstance(row, str):
                    cleaned_row = row.strip("[]")
                    parts = [p.strip() for p in cleaned_row.split(',')]
                    if len(parts) >= 2:
                        golden_date_str = parts[1]
                    else:
                        continue
                else:
                    # Fallback if it is actually a list
                    golden_date_str = row[1]

                golden_date = datetime.strptime(golden_date_str, '%d.%m.%Y')
                
                # if golden date is newer or equal to the date in excel it is paid
                if golden_date >= excel_date:
                    return True
            except Exception as e:
                # print(f"Error checking date for row {row}: {e}")
                continue
    except Exception as e:
        print(f"Error parsing excel date {date_of_payment}: {e}")
        return False
        
    return False
    
def check_owed(keyword, payment_owed):
    return any(keyword in row for row in payment_owed)

def check_owed_with_amount(keyword, payment_owed, expected_amount):
    """Check if keyword exists in payment_owed AND the TUTAR matches expected_amount.
    If keyword is None, just checks if expected_amount exists in any row."""
    
    for row in payment_owed:
        # If keyword provided, require it to be in the row
        if keyword is not None and keyword not in row:
            continue
        # Extract amount from row - Turkish format like "1.600,00" at the end
        amounts = re.findall(r'[\d.]+,\d{2}', row)
        if amounts:
            tutar_str = amounts[-1]
            tutar = int(float(tutar_str.replace('.', '').replace(',', '.')))
            if tutar == expected_amount:
                return True
    return False

def check_paid(keyword, payments_paid):
    return any(keyword in row for row in payments_paid)

def infer_payment_type_from_amount(payment_amount):
    """Infer payment type from amount alone (used when name search fails)."""
    if payment_amount == 1200 or payment_amount == 900:
        return "YAZILI SINAV HARCI"
    if payment_amount == 1600 or payment_amount == 1350:
        return "UYGULAMA SINAV HARCI"
    if payment_amount == 1000:
        return "BELGE ÜCRETİ"
    if payment_amount >= 2000 and payment_amount < 4000 and payment_amount%500 == 0:
        return "TAKSİT"
    if payment_amount == 4000:
        return "DORTBIN"  # Could be ÖZEL DERS or BAŞARISIZ - still ambiguous
    if payment_amount > 1600:
        if (payment_amount - 1600) % 500 == 0:
            return "UYGULAMA SINAV HARCI"  # Likely UYGULAMA + TAKSİT
        if (payment_amount - 1200) % 500 == 0:
            return "YAZILI SINAV HARCI"  # Likely YAZILI + TAKSİT
    return "BILINMIYOR"  # Unknown
    
async def human_option_select(page, dropdown_selector, option_text):
    dropDownList = page.locator(dropdown_selector)
    await dropDownList.select_option(option_text)
    
async def human_button_click(page, selector=None, has_text=None ,exact_text=None, check_exists=False, timeout=5000):
    """
    Click an element with human-like behavior.
    Returns True on success, False on failure (instead of crashing).
    timeout: max wait time in ms (default 5000ms instead of 30000ms)
    """
    try:
        if exact_text:
            element = page.get_by_text(exact_text, exact=False)
        elif selector and has_text:
            has_text_tr = turkish_pattern_check(has_text)
            element = page.locator(selector).filter(has_text=has_text_tr).first
        elif selector:
            element = page.locator(selector).first
        else:
            print("No selector provided")
            return False

        if check_exists:
            try:
                # Wait up to 3000ms (3 seconds) for the element to appear
                await element.wait_for(state="visible", timeout=3000)
            except:
                # If it times out, print message and STOP function here
                print(f"The name '{exact_text or selector}' is not there.")
                return False

        # Use shorter timeout for hover/click to fail fast
        await element.hover(timeout=timeout)

        await asyncio.sleep(random.uniform(0.3, 0.7))

        await element.click(timeout=timeout)
        return True
    except Exception as e:
        print(f"human_button_click failed for '{has_text or exact_text or selector}': {e}")
        return False

async def human_type(page, selector, text):
    element = page.locator(selector).first

    await element.hover()
    await asyncio.sleep(random.uniform(0.2,0.5))

    await element.click()

    # Clear the field by selecting all and deleting (human-like behavior)
    # Use Cmd+A on macOS, Ctrl+A on Linux/Windows
    select_all_key = "Meta+a" if sys.platform == "darwin" else "Control+a"
    await page.keyboard.press(select_all_key)
    await asyncio.sleep(random.uniform(0.1, 0.2))

    await element.type(text, delay=random.randint(50,150))

async def get_human_name(description):

    if re.findall("^PK", description):
        return "PAYMENT_BY_POS"
    if re.findall("^FAST", description):
        parts = description.split("-")
        #isim
        name = parts[1]
        #aciklama - everything after the second "-"
        info = "-".join(parts[2:]) if len(parts) > 2 else ""
        #print("FAST",name,info)
        if len(info) == 0:
            return name
        else:
            try:
                response = requests.post(
                    url="https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer " + os.getenv("OPENROUTER_API_KEY"),
                    },
                    data=json.dumps({
                    "model": "google/gemini-3-flash-preview", 
                        "messages": [
                            {'role': 'system', 'content': SYSTEM_PROMPT},
                            {'role': 'user', 'content': f"Description: {info} | Sender: {name}"}
                        ]
                    })
                )
                
                if response.status_code == 200:
                    content = response.json()['choices'][0]['message']['content']
                    # Clean code blocks if present
                    content = content.replace('```json', '').replace('```', '').strip()
                    names = json.loads(content)
                    if names:
                        # Return the first name found
                        return names[0] 
                    else:
                        # LLM found no names in description, so return the Sender Name
                        return name
            except Exception as e:
                print(f"LLM Error: {e}")
                
            return name

    elif re.findall("^CEP ŞUBE", description):
        parts = description.split("-")
        #name - everything after the last "-"
        name = parts[-1]
        #aciklama - everything between first "-" and last "-"
        info = "-".join(parts[1:-1]) if len(parts) > 2 else ""
        #print("CEP",name,info.strip()+"info")
        if len(info.strip()) == 0:
            return name
        else:
            try:
                response = requests.post(
                    url="https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer " + os.getenv("OPENROUTER_API_KEY"),
                    },
                    data=json.dumps({
                    "model": "google/gemini-3-flash-preview", 
                        "messages": [
                            {'role': 'system', 'content': SYSTEM_PROMPT},
                            {'role': 'user', 'content': f"Description: {info} | Sender: {name}"}
                        ]
                    })
                )
                if response.status_code == 200:
                    content = response.json()['choices'][0]['message']['content']
                    content = content.replace('```json', '').replace('```', '').strip()
                    names = json.loads(content)
                    if names:
                        return names[0]
                    else:
                        return name
            except Exception as e:
                print(f"LLM Error: {e}")

            return name
    
    # Fallback for any other format (e.g. EF5600706 MEHMET İDRİS AKTAŞ...)
    else:
        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + os.getenv("OPENROUTER_API_KEY"),
                },
                data=json.dumps({
                "model": "google/gemini-3-flash-preview", 
                    "messages": [
                        {'role': 'system', 'content': SYSTEM_PROMPT},
                        {'role': 'user', 'content': f"Description: {description} | Sender: UNKNOWN"}
                    ]
                })
            )
            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content']
                content = content.replace('```json', '').replace('```', '').strip()
                names = json.loads(content)
                if names:
                    return names[0]
        except Exception as e:
            print(f"LLM Error in fallback: {e}")

    return "Error 401: No name found"   


async def clean_payment_row(row_text):
    # Regex to find amounts (e.g. 1.000,00 or 500,00)
    # This is more reliable than LLM for numbers
    amounts = re.findall(r'\b\d{1,3}(?:\.\d{3})*,\d{2}\b', row_text)
    regex_amount = amounts[-1] if amounts else None

    # Regex to find dates (DD.MM.YYYY)
    dates = re.findall(r'\d{2}\.\d{2}\.\d{4}', row_text)
    regex_date = None
    if dates:
        # Logic: If Paid (ÖDEDİ) and 2 dates, use 2nd. Else 1st.
        if "ÖDEDİ" in row_text and len(dates) >= 2:
            regex_date = dates[1]
        else:
            regex_date = dates[0]

    # Use LLM to clean the messy OCR row into a structured format
    prompt = 'You are a data cleaner. Extract the Payment Type, Date, Amount, and Status from this messy OCR text. \n\nRules:\n1. Output strictly a list of 4 items: [Type, Date, Amount, Status].\n2. Status must be "ÖDEDİ" or "ÖDEMEDİ".\n3. Payment Type MUST be one of these EXACT strings (fix any OCR errors to match these):\n   - "YAZILI SINAV HARCI"\n   - "UYGULAMA SINAV HARCI"\n   - "BAŞARISIZ ADAY EĞİTİMİ"\n   - "ÖZEL DERS"\n   - "BELGE ÜCRETİ"\n   - "TAKSİT"\n4. If the text contains "YZL" AND "SNV", map it to "YAZILI SINAV HARCI".\n5. If the text contains "UYG" AND "SNV", map it to "UYGULAMA SINAV HARCI".\n6. If the text contains "BASARISIZ", "ADAY", "EGITIMI", map it to "BAŞARISIZ ADAY EĞİTİMİ".\n7. If the text contains "OZEL", "DERS", map it to "ÖZEL DERS".\n8. If the text contains "BELGE", "UCRETI", map it to "BELGE ÜCRETİ".\n9. If the text contains "TAKSİT", "TAKSIT", "TKST", map it to "TAKSİT". DO NOT CHANGE "TAKSİT" TO "BELGE ÜCRETİ".\n10. DATE RULE: If the row has TWO dates (e.g. 03.12.2025 and 11.12.2025), the second one is the Payment Date. If Status is "ÖDEDİ", you MUST use the SECOND date. If "ÖDEMEDİ", use the first/only date.\n11. IGNORE the word "YAZDIR". It is NOT "YAZILI".\n12. IGNORE integer numbers like "5618" or "8363" (these are Receipt IDs). The Amount ALWAYS has a comma (e.g. 1.000,00).\n\nExample 1:\nInput: "ÖDEMEDİ UYG 05.12.2025 SNV. 600,00 AVUKAT HARCI"\nOutput: [UYGULAMA SINAV HARCI, 05.12.2025, 600,00, ÖDEMEDİ]\n\nExample 2:\nInput: "TAKSİT 03.12.2025 11.12.2025 5.000,00 ÖDEDİ"\nOutput: [TAKSİT, 11.12.2025, 5.000,00, ÖDEDİ]\n\nInput: ' + row_text + '\nOutput ONLY the list format like the examples.'

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + os.getenv("OPENROUTER_API_KEY"),
            },
            data=json.dumps({
                "model": "google/gemini-3-flash-preview", 
                "messages": [
                    {'role': 'user', 'content': prompt}
                ]
            })
        )
        
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            if lines:
                cleaned_list_str = lines[0]
                
                # Apply Regex Overrides
                try:
                    # Remove brackets and split
                    parts = [p.strip() for p in cleaned_list_str.strip('[]').split(',')]
                    if len(parts) >= 4:
                        # Override Date (Index 1)
                        if regex_date:
                            parts[1] = regex_date
                        
                        # Override Amount (Index 2)
                        if regex_amount:
                            parts[2] = regex_amount
                            
                        cleaned_list_str = f"[{', '.join(parts)}]"
                        return cleaned_list_str
                except:
                    pass # Fallback to LLM output if parsing fails
                
                return cleaned_list_str
    except Exception as e:
        print(f"LLM Error in clean_payment_row: {e}")

    return row_text

# Global reader to avoid reloading model
reader = None

async def image_ocr(screenshot):
    global reader
    if reader is None:
        print("Initializing EasyOCR with GPU...")
        reader = easyocr.Reader(['tr', 'en'], gpu=True)

    # 1. Read and Sort by Y (vertical position) first to group lines
    # mag_ratio=2 enlarges the image internally, helping with small text/numbers
    results = sorted(reader.readtext(screenshot, mag_ratio=2), key=lambda r: r[0][0][1])

    rows = []
    for bbox, text, _ in results:
        # Get coordinates (Top-Left)
        top_left = bbox[0]
        x, y = top_left[0], top_left[1]
        
        # 2. Group by Row (Y proximity)
        # We store (x, text) tuples now, so we can sort them left-to-right later
        # 2. Group by Row (Y proximity)
        # We store (x, text) tuples now, so we can sort them left-to-right later
        # Increased threshold to 50 to handle multi-line text wrapping (e.g. BASARISIZ ADAY EĞİTİMİ)
        if not rows or abs(y - rows[-1][0]) > 50:
            rows.append([y, [(x, text)]])
        else:
            rows[-1][1].append((x, text))

    return rows
async def get_payment_type(page, name_surname, payment_amount, date_of_payment, search_new_person=True, cached_data=None):

    #ENTER THE PERSONS PAGE AND TAKE A SCREENSHOT OF ALL PAYMENTS MADE AND PAYMENTS OWED
    if search_new_person:

        print("Clicking KURSİYER ARA...")
        await human_button_click(page, "a.btn.bg-orange", has_text="KURSİYER ARA")
        await asyncio.sleep(random.uniform(1.7, 3.7))
        
        await human_type(page, "#txtaraadi", name_surname)
        await asyncio.sleep(random.uniform(0.8, 1.8))
        await page.keyboard.press("Enter")
        await asyncio.sleep(random.uniform(1.7, 3.7))

        # Dead screen check - verify the name appears in results (Case Insensitive Check)
        # We use a comma-separated selector list which acts as an OR operator in CSS
        

        #success_indicator = page.locator(f"a:has-text('{name_surname}'), a:has-text('{name_surname.upper()}'), a:has-text('{UnicodeString(name_surname).toLower(tr)}'), a:has-text('{UnicodeString(name_surname).toUpper(tr)}'), a::has-text('{UnicodeString(name_surname.split(" ")[0]).toUpper(tr)}')").first
        name_pattern = turkish_pattern_check(name_surname)
        success_indicator = page.get_by_role("link", name=name_pattern)
        try:
            await success_indicator.wait_for(state="visible", timeout=3000)
        except:
            # First attempt failed - retry with just surname
            print(f"Name not found - retrying with surname only...")
            await human_button_click(page, "a.btn.bg-orange", has_text="KURSİYER ARA")
            await asyncio.sleep(random.uniform(1.7, 3.7))
            
            surname = name_surname.split(" ")[-1]  # Get last part as surname
            await human_type(page, "#txtaraadi", surname)
            await asyncio.sleep(random.uniform(0.8, 1.8))
            await page.keyboard.press("Enter")
            await asyncio.sleep(random.uniform(1.7, 3.7))
            
            try:
                # Same combined check for surname
                success_indicator = page.locator(f"a:has-text('{surname}'), a:has-text('{surname.upper()}'), a:has-text('{str(UnicodeString(surname).toLower(tr))}'), a:has-text('{str(UnicodeString(surname).toUpper(tr))}')").first
                await success_indicator.wait_for(state="visible", timeout=3000)
            except:
                print(f"Both attempts failed for '{name_surname}'")
                # Infer payment type from amount even when name not found
                inferred_type = infer_payment_type_from_amount(payment_amount)
                return [[inferred_type, "FLAG: 404"]], None

    payment_owed = []
    payments_paid = []
    payments_taksit_paid = []
    payments_taksit_owed = []

    if not search_new_person and cached_data:
        print(f"Using cached OCR data for {name_surname}")
        payment_owed, payments_paid, payments_taksit_paid, payments_taksit_owed = cached_data
    else:
        # Click on the person's name to go to payment page
        name_click_success = await human_button_click(page, "a", has_text=name_surname)
        if not name_click_success:
            # Try with surname only as fallback
            surname = name_surname.split(" ")[-1]
            name_click_success = await human_button_click(page, "a", has_text=surname)
            if not name_click_success:
                print(f"Failed to click on name '{name_surname}' - skipping to next person")
                inferred_type = infer_payment_type_from_amount(payment_amount)
                return [[inferred_type, "FLAG: 404"]], None

        await asyncio.sleep(random.uniform(1.7, 3.7))

        odeme_click_success = await human_button_click(page, "a:visible", has_text="ÖDEME")
        if not odeme_click_success:
            print(f"Failed to click ÖDEME button for '{name_surname}' - skipping")
            inferred_type = infer_payment_type_from_amount(payment_amount)
            return [[inferred_type, "FLAG: 404"]], None
        print("in the ODEME page")

        tables = page.locator("table.table.table-bordered.dataTable")
        
        # First table
        await tables.nth(0).screenshot(path="screenshotv2.png")
        
        # Second table
        await tables.nth(1).screenshot(path="screenshotv3.png")


        #GO THROUGH THE SCREENSHOT WITH OCR AND ORGANIZE IT IN ARRAYS
        print("Starting OCR on screenshots...")
        payments_taksit_info = await image_ocr("screenshotv2.png")
        payments_info = await image_ocr("screenshotv3.png")
        print(f"OCR Complete. Found {len(payments_info)} rows in payments and {len(payments_taksit_info)} rows in taksit.")

        for row in payments_taksit_info:
            if len(row) < 2:
                continue

            sorted_items = sorted(row[1], key=lambda item: item[0])
            
            if len(sorted_items) < 2:
                continue
            
            # Find payment type (skip leading row numbers)
            payment_type_text = None
            for x, text in sorted_items:
                if not text.strip().isdigit():
                    payment_type_text = text
            # Join all text in the row to form a single string
            row_text = " ".join([text for x, text in sorted_items])
            
            # Skip header rows
            # Skip header rows (Case insensitive and more robust)
            row_upper = row_text.upper()
            if "TIPI" in row_upper or "TİPİ" in row_upper or "BORÇ" in row_upper or "DURUMU" in row_upper or "VADE" in row_upper:
                print(f"Skipping header row: {row_text}")
                continue
            
            # Clean the row with LLM
            cleaned_row = await clean_payment_row(row_text)
            print(f"Original: {row_text} -> Cleaned: {cleaned_row}")
            
            if "ÖDEDİ" in row_text:
                payments_taksit_paid.append(cleaned_row)
            else: 
                payments_taksit_owed.append(cleaned_row)

        for row in payments_info:
            if len(row) < 2:
                continue
            # row = [y_position, [(x, text), (x, text), ...]]
            # Sort by X position to get left-to-right order
            sorted_items = sorted(row[1], key=lambda item: item[0])
            
            if len(sorted_items) < 2:
                continue
            
            # Find payment type (skip leading row numbers)
            payment_type_text = None
            for x, text in sorted_items:
                if not text.strip().isdigit():
                    payment_type_text = text
            # Join all text in the row to form a single string
            row_text = " ".join([text for x, text in sorted_items])

            # Skip header rows (Case insensitive and more robust)
            row_upper = row_text.upper()
            if "TIPI" in row_upper or "TİPİ" in row_upper or "BORÇ" in row_upper or "DURUMU" in row_upper or "VADE" in row_upper:
                print(f"Skipping header row: {row_text}")
                continue
            
            # Clean the row with LLM
            print(f"Processing row with LLM: {row_text}")
            cleaned_row = await clean_payment_row(row_text)
            print(f"Original: {row_text} -> Cleaned: {cleaned_row}")
            
            if "ÖDEDİ" in row_text:
                payments_paid.append(cleaned_row)
            else: 
                # We store the full row text now, logic will check for substrings
                payment_owed.append(cleaned_row)

        print(f"Payments Owed: {payment_owed}")
        print(f"Payments Paid: {payments_paid}")
        print(f"Taksit Owed: {payments_taksit_owed}")
        print(f"Taksit Paid: {payments_taksit_paid}")
    
    # Update cache
    cached_data = (payment_owed, payments_paid, payments_taksit_paid, payments_taksit_owed)

    payment_types = []

    #CALCULATE WHAT IS BEING PAID ACTUALLY
    #SIMPLE PAYMENTS
    print(f"Calculating logic for amount: {payment_amount}")
    
    # Helper to check if any row contains the keyword
    

    if payment_amount == 1200 or payment_amount == 900:
        # Check OWED first with exact amount match - person may have retaken exam after failing
        if check_owed_with_amount("YZL. SNV. HARCI", payment_owed, payment_amount) or check_owed_with_amount("YAZILI SINAV HARCI", payment_owed, payment_amount):
            print(f"Logic: {payment_amount} -> YAZILI SINAV HARCI (BORC VAR - amount matches)")
            payment_types.append(["YAZILI SINAV HARCI", "BORC VAR", payment_amount])
            return payment_types, cached_data
        elif check_paid("YZL. SNV. HARCI", payments_paid) or check_paid("YAZILI SINAV HARCI", payments_paid):
            print(f"Logic: {payment_amount} -> YAZILI SINAV HARCI (BORC ODENMIS)")
            payment_types.append(["YAZILI SINAV HARCI", "BORC ODENMIS", payment_amount])
            return payment_types, cached_data
        else:
            payment_types.append(["YAZILI SINAV HARCI", "BORC YOK", payment_amount])
            return payment_types, cached_data
    if payment_amount == 1600 or payment_amount == 1350:
        # Check OWED first with exact amount match - person may have retaken exam after failing
        if check_owed_with_amount("UYG. SNV. HARCI", payment_owed, payment_amount) or check_owed_with_amount("UYGULAMA SINAV HARCI", payment_owed, payment_amount):
            print(f"Logic: {payment_amount} -> UYGULAMA SINAV HARCI (BORC VAR - amount matches)")
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR", payment_amount])
            return payment_types, cached_data
        elif check_paid("UYG. SNV. HARCI", payments_paid) or check_paid("UYGULAMA SINAV HARCI", payments_paid):
            print(f"Logic: {payment_amount} -> UYGULAMA SINAV HARCI (BORC ODENMIS)")
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS", payment_amount])
            return payment_types, cached_data
        else:
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC YOK", payment_amount])
            return payment_types, cached_data

    if payment_amount == 4000 and check_owed("BAŞARISIZ ADAY EĞİTİMİ", payment_owed):
        payment_types.append(["BAŞARISIZ ADAY EĞİTİMİ", "BORC VAR"])
        return payment_types, cached_data
    elif payment_amount == 4000 and check_paid("BAŞARISIZ ADAY EĞİTİMİ", payments_paid):
        payment_types.append(["BAŞARISIZ ADAY EĞİTİMİ", "BORC ODENMIS"])
        return payment_types, cached_data

    # If exactly 4000 and there's a 4000 taksit owed, pay it as TAKSİT (not ambiguous)
    if payment_amount == 4000 and check_owed_with_amount(None, payments_taksit_owed, 4000):
        payment_types.append(["TAKSİT", "BORC VAR"])
        return payment_types, cached_data

    if payment_amount == 4000:
        payment_types.append(["DORTBIN", "FLAG: 4000"])
        return payment_types, cached_data

    #if payment_amount == 4000 and check_owed("ÖZEL DERS", payment_owed):
    #    payment_types.append(["ÖZEL DERS", "BORC VAR"])
    #    return payment_types

    payment_copy = payment_amount
    
    if payment_amount >= 2000 and payment_amount%500 == 0 and payment_amount < 4000 :
        if check_owed("BELGE ÜCRETİ", payment_owed):
            payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
            payment_types.append(["TAKSİT", "BORC VAR"])
        elif check_owed("TAKSİT", payments_taksit_owed):
            if check_date_if_paid(date_of_payment, payments_taksit_paid):
                 payment_types.append(["TAKSİT", "BORC ODENMIS"])
            else:
                 payment_types.append(["TAKSİT", "BORC VAR"])
            return payment_types, cached_data
            
        if check_paid("TAKSİT", payments_taksit_paid) and check_date_if_paid(date_of_payment, payments_taksit_paid):
            payment_types.append(["TAKSİT", "BORC ODENMIS"])
        return payment_types, cached_data
    
    #COMPLEX PAYMENTS
    if payment_copy > 1600:
        
        # NEW: Flexible remainder matching - if remainder equals any owed taksit amount
        # Example: 2200 - 1600 = 600, and TAKSİT 600 is owed → UYGULAMA + TAKSİT
        remainder_uygulama = payment_copy - 1600
        remainder_yazili = payment_copy - 1200
        
        if check_owed_with_amount("UYG. SNV. HARCI", payment_owed, 1600) or check_owed_with_amount("UYGULAMA SINAV HARCI", payment_owed, 1600):
            if check_owed_with_amount(None, payments_taksit_owed, remainder_uygulama):
                print(f"Logic: {payment_copy} = 1600 (UYGULAMA) + {remainder_uygulama} (TAKSİT owed)")
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
                return payment_types, cached_data
        
        if check_owed_with_amount("YZL. SNV. HARCI", payment_owed, 1200) or check_owed_with_amount("YAZILI SINAV HARCI", payment_owed, 1200):
            if check_owed_with_amount(None, payments_taksit_owed, remainder_yazili):
                print(f"Logic: {payment_copy} = 1200 (YAZILI) + {remainder_yazili} (TAKSİT owed)")
                payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
                return payment_types, cached_data
        
        # Original modulo-based logic (fallback)
        if (payment_copy - 1600)%500 == 0 and payment_copy - 1600 != 4000 and (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed) or check_paid("UYG. SNV. HARCI", payments_paid) or check_paid("UYGULAMA SINAV HARCI", payments_paid)):
            if (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed)):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("UYG. SNV. HARCI", payments_paid) or check_paid("UYGULAMA SINAV HARCI", payments_paid):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["TAKSİT", "BORC ODENMIS"])
        elif (payment_copy - 1600)%500 == 0 and payment_copy - 1600 == 4000 and (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed) or check_paid("UYG. SNV. HARCI", payments_paid) or check_paid("UYGULAMA SINAV HARCI", payments_paid)) and check_owed_with_amount(None, payments_taksit_owed, 4000):
            if (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed)):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("UYG. SNV. HARCI", payments_paid) or check_paid("UYGULAMA SINAV HARCI", payments_paid):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["TAKSİT", "BORC ODENMIS"])
            elif check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC YOK"])
        elif (payment_copy - 1200)%500 == 0 and payment_copy - 1200 != 4000 and (check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed) or check_paid("YZL. SNV. HARCI", payments_paid) or check_paid("YAZILI SINAV HARCI", payments_paid)):    
            if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])
            elif check_paid("YZL. SNV. HARCI", payments_paid) or check_paid("YAZILI SINAV HARCI", payments_paid):
                payment_types.append(["YAZILI SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["DORTBIN", "BORC ODENMIS"])
            else:
                payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])

        elif (payment_copy - 1200)%500 == 0 and payment_copy - 1200 == 4000 and (check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed) or check_paid("YZL. SNV. HARCI", payments_paid) or check_paid("YAZILI SINAV HARCI", payments_paid)) and check_owed_with_amount(None, payments_taksit_owed, 4000):
            if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("YZL. SNV. HARCI", payments_paid) or check_paid("YAZILI SINAV HARCI", payments_paid) and check_date_if_paid(date_of_payment, payments_taksit_paid):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["TAKSİT", "BORC ODENMIS"])
            else:
                payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
                payment_types.append(["TAKSİT", "BORC YOK"])
        
        elif (payment_copy - 1000)%500 == 0 and payment_copy - 1000 != 4000 and (check_owed("BELGE ÜCRETİ", payment_owed) or check_paid("BELGE ÜCRETİ", payments_paid)):
            if  check_owed("BELGE ÜCRETİ", payment_owed):
                payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("BELGE ÜCRETİ", payments_paid):
                payment_types.append(["BELGE ÜCRETİ", "BORC ODENMIS"])
                if check_date_if_paid(date_of_payment, payments_taksit_paid):
                    payment_types.append(["TAKSİT", "BORC ODENMIS"])
                else: 
                    payment_types.append(["TAKSİT", "BORC VAR"])
        
        elif (payment_copy - 1000)%500 == 0 and payment_copy - 1000 == 4000 and (check_owed("BELGE ÜCRETİ", payment_owed) or check_paid("BELGE ÜCRETİ", payments_paid)) and check_owed_with_amount(None, payments_taksit_owed, 4000) :
            if check_owed("BELGE ÜCRETİ", payment_owed):
                payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("BELGE ÜCRETİ", payments_paid):
                payment_types.append(["BELGE ÜCRETİ", "BORC ODENMIS"])
                payment_types.append(["DORTBIN", "BORC ODENMIS"])
        
        # NEW: BAŞARISIZ ADAY EĞİTİMİ combo logic
        # If payment > 4000, owes BAŞARISIZ 4000, and remainder matches any other owed item
        elif payment_copy > 4000 and check_owed("BAŞARISIZ ADAY EĞİTİMİ", payment_owed):
            remainder_basarisiz = payment_copy - 4000
            # Check if remainder matches any owed amount in payment_owed OR payments_taksit_owed
            if check_owed_with_amount(None, payment_owed, remainder_basarisiz) or check_owed_with_amount(None, payments_taksit_owed, remainder_basarisiz):
                print(f"Logic: {payment_copy} = 4000 (BAŞARISIZ) + {remainder_basarisiz} (other owed)")
                payment_types.append(["BAŞARISIZ ADAY EĞİTİMİ", "BORC VAR"])
                # Figure out what the remainder is
                if check_owed_with_amount(None, payments_taksit_owed, remainder_basarisiz):
                    payment_types.append(["TAKSİT", "BORC VAR"])
                elif check_owed_with_amount("ÖZEL DERS", payment_owed, remainder_basarisiz):
                    payment_types.append(["ÖZEL DERS", "BORC VAR"])
                elif check_owed_with_amount("BELGE ÜCRETİ", payment_owed, remainder_basarisiz):
                    payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
                elif check_owed_with_amount("UYG", payment_owed, remainder_basarisiz) or check_owed_with_amount("UYGULAMA", payment_owed, remainder_basarisiz):
                    payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                elif check_owed_with_amount("YZL", payment_owed, remainder_basarisiz) or check_owed_with_amount("YAZILI", payment_owed, remainder_basarisiz):
                    payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                else:
                    payment_types.append(["BILINMIYOR", f"FLAG: {remainder_basarisiz}"])
                return payment_types, cached_data
        
        elif payment_copy > 4000 and check_owed("TAKSİT", payments_taksit_owed) and get_owed_taksit(payments_taksit_owed) >= payment_copy and not check_date_if_paid(date_of_payment, payments_taksit_paid):
            payment_types.append(["TAKSİT", "BORC VAR"])

    return payment_types, cached_data


