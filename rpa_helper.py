import asyncio
import random
import time
import os
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

SYSTEM_PROMPT = """You are an expert entity extraction system specialized in identifying Turkish human names in payment descriptions.
Your goal is to extract ALL full human names found in the text that are DIFFERENT from the 'Sender Name'.

Rules:
1. Input will be: "Description: [text] | Sender: [name]"
2. Extract full names (First + Last Name). Ignore single names unless context clearly implies a person.
3. Ignore company names, bank terms (KURS, ODEME, HESAP, YAPI, KREDI), and the Sender's name.
4. Output MUST be a strictly valid JSON list of strings. Example: ["Ali Yilmaz", "Ayse Demir"]
5. If no valid names are found, output empty list: []
"""

def check_date_if_paid(date_of_payment, payments_paid):
    if len(payments_paid) == 0:
        return False
    try:
        excel_date = datetime.strptime(date_of_payment, '%d.%m.%Y')
        for row in payments_paid:
            # row is [Type, Date, Amount, Status]
            # row[1] is the Date string
            golden_date_str = row[1]
            try:
                golden_date = datetime.strptime(golden_date_str, '%d.%m.%Y')
                
                # if golden date is newer or equal to the date in excel it is paid
                if golden_date >= excel_date:
                    return True
            except:
                continue
    except ValueError:
        return False
        
    return False
    
def check_owed(keyword, payment_owed):
    return any(keyword in row for row in payment_owed)

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
    
async def human_button_click(page, selector=None, has_text=None ,exact_text=None, check_exists=False):
    if exact_text:
        element = page.get_by_text(exact_text, exact=False)
    elif selector and has_text:
        element = page.locator(selector).filter(has_text=has_text).first
    elif selector:
        element = page.locator(selector).first
    else:
        print("No selector provided")
        return
    
    if check_exists:
        try:
            # Wait up to 3000ms (3 seconds) for the element to appear
            await element.wait_for(state="visible", timeout=3000)
        except:
            # If it times out, print message and STOP function here
            print(f"The name '{exact_text or selector}' is not there.")
            return
            
    await element.hover()
    
    await asyncio.sleep(random.uniform(0.3, 0.7))
    
    await element.click()

async def human_type(page, selector, text):
    element = page.locator(selector).first

    await element.hover()
    await asyncio.sleep(random.uniform(0.2,0.5))

    await element.click()

    # Clear the field by selecting all and deleting (human-like behavior)
    await page.keyboard.press("Control+a")
    await asyncio.sleep(random.uniform(0.1, 0.2))

    await element.type(text, delay=random.randint(50,150))

async def get_human_name(description):

    if re.findall("^PK", description):
        return "PAYMENT_BY_POS"
    if re.findall("^FAST", description):
        parts = re.split("-", description)
        #isim
        name = parts[1]  
        #aciklama
        info = parts[2]
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
                    "model": "openai/gpt-oss-120b", 
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
                        # Return the first name found, or join them if multiple?
                        # For RPA compatibility, returning the first valid name is safest.
                        # If user wants all, we can join them, but search might fail.
                        # Returning the first one for now as it's the most likely intended target.
                        return names[0] 
            except Exception as e:
                print(f"LLM Error: {e}")
                
            return name

    elif re.findall("^CEP ŞUBE", description):
        parts = re.split("-", description)
        info = parts[2]
        name = parts[3]
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
                    "model": "openai/gpt-oss-120b", 
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
            except Exception as e:
                print(f"LLM Error: {e}")

            return name
    
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
                "model": "openai/gpt-oss-120b", 
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

async def image_ocr(screenshot):
    # Use CPU to avoid OOM with high mag_ratio
    reader = easyocr.Reader(['tr', 'en'], gpu=False)

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
async def get_payment_type(page, name_surname, payment_amount, date_of_payment, search_new_person=True):

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
        success_indicator = page.locator(f"a:has-text('{name_surname}'), a:has-text('{name_surname.upper()}'), a:has-text('{name_surname.lower()}')").first
        
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
                success_indicator = page.locator(f"a:has-text('{surname}'), a:has-text('{surname.upper()}'), a:has-text('{surname.lower()}')").first
                await success_indicator.wait_for(state="visible", timeout=3000)
            except:
                print(f"Both attempts failed for '{name_surname}'")
                # Infer payment type from amount even when name not found
                inferred_type = infer_payment_type_from_amount(payment_amount)
                return [[inferred_type, "FLAG: 404"]]

        await human_button_click(page, "a", has_text=name_surname)
        await asyncio.sleep(random.uniform(1.7, 3.7))
        
        await human_button_click(page, "a:visible", has_text="ÖDEME")
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

    payment_types = []
    payment_owed = []
    payments_paid = []
    payments_taksit_paid = []
    payments_taksit_owed = []

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
        
        # Clean the row with LLM
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

    #CALCULATE WHAT IS BEING PAID ACTUALLY
    #SIMPLE PAYMENTS
    print(f"Calculating logic for amount: {payment_amount}")
    
    # Helper to check if any row contains the keyword
    

    if payment_amount == 1200 or payment_amount == 900:
        if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
            print("Logic: 1200 -> YAZILI SINAV HARCI (BORC VAR)")
            payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
            return payment_types
        elif check_paid("YZL. SNV. HARCI", payments_paid):
            payment_types.append(["YAZILI SINAV HARCI", "BORC ODENMIS"])
            return payment_types
        else: 
            payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
            return payment_types
    if payment_amount == 1600 or payment_amount == 1350:
        if check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed):
            print("Logic: 1600 -> UYGULAMA SINAV HARCI (BORC VAR)")
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
            return payment_types

        elif check_paid("UYG. SNV. HARCI", payments_paid):
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS"])
            return payment_types
        else: 
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC YOK"])
            return payment_types

    if payment_amount == 4000 and check_owed("BAŞARISIZ ADAY EĞİTİMİ", payment_owed):
        payment_types.append(["BAŞARISIZ ADAY EĞİTİMİ", "BORC VAR"])
        return payment_types
    elif payment_amount == 4000 and check_paid("BAŞARISIZ ADAY EĞİTİMİ", payments_paid):
        payment_types.append(["BAŞARISIZ ADAY EĞİTİMİ", "BORC ODENMIS"])
        return payment_types

    if payment_amount == 4000:
        payment_types.append(["DORTBIN", "FLAG: 4000"])
        return payment_types

    #if payment_amount == 4000 and check_owed("ÖZEL DERS", payment_owed):
    #    payment_types.append(["ÖZEL DERS", "BORC VAR"])
    #    return payment_types

    payment_copy = payment_amount

    if payment_amount >= 2000 and payment_amount%500 == 0 and payment_amount < 4000 :
        if check_owed("BELGE ÜCRETİ", payment_owed):
            payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
            payment_types.append(["TAKSİT", "BORC VAR"])
        elif check_owed("TAKSİT", payment_owed):
            if check_date_if_paid(date_of_payment, payments_taksit_paid):
                 payment_types.append(["TAKSİT", "BORC ODENMIS"])
            else:
                 payment_types.append(["TAKSİT", "BORC VAR"])
            return payment_types
            
        if check_paid("TAKSİT", payments_taksit_paid) and check_date_if_paid(date_of_payment, payments_taksit_paid):
            payment_types.append(["TAKSİT", "BORC ODENMIS"])
        return payment_types
    
    #COMPLEX PAYMENTS
    if payment_copy > 1600:
        if (payment_copy - 1600)%500 == 0 and payment_copy - 1600 != 4000:
            if (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed)):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("UYG. SNV. HARCI", payments_paid):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["TAKSİT", "BORC ODENMIS"])
        elif (payment_copy - 1600)%500 == 0 and payment_copy - 1600 == 4000:
            if (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed)):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])
            elif check_paid("UYG. SNV. HARCI", payments_paid):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["DORTBIN", "BORC ODENMIS"])
            elif check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC YOK"])
            
        elif (payment_copy - 1200)%500 == 0 and payment_copy - 1200 != 4000:
            if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("YZL. SNV. HARCI", payments_paid):
                payment_types.append(["YAZILI SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["TAKSİT", "BORC ODENMIS"])
            else:
                payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
                payment_types.append(["TAKSİT", "BORC VAR"])

        elif (payment_copy - 1200)%500 == 0 and payment_copy - 1200 == 4000:
            if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])
            elif check_paid("YZL. SNV. HARCI", payments_paid):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC ODENMIS"])
                payment_types.append(["DORTBIN", "BORC ODENMIS"])
            else:
                payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])

        elif (payment_copy - 1000)%500 == 0 and payment_copy - 1000 != 4000:
            if check_owed("BELGE ÜCRETİ", payment_owed):
                payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_paid("BELGE ÜCRETİ", payments_paid):
                payment_types.append(["BELGE ÜCRETİ", "BORC ODENMIS"])
                if check_date_if_paid(date_of_payment, payments_paid):
                    payment_types.append(["TAKSİT", "BORC ODENMIS"])
                else: 
                    payment_types.append(["TAKSİT", "BORC VAR"])
        
        elif (payment_copy - 1000)%500 == 0 and payment_copy - 1000 == 4000:
            if check_owed("BELGE ÜCRETİ", payment_owed):
                payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])
            elif check_paid("BELGE ÜCRETİ", payments_paid):
                payment_types.append(["BELGE ÜCRETİ", "BORC ODENMIS"])
                payment_types.append(["DORTBIN", "BORC ODENMIS"])

    return payment_types


