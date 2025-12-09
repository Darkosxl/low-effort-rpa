import asyncio
import random
import time
import os
from twilio.rest import Client
import pandas as pd
import easyocr
from ollama import chat
from ollama import ChatResponse
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import dotenv
import operator

def check_owed(keyword, payment_owed):
    return any(keyword in row for row in payment_owed)

def check_paid(keyword, payments_paid):
    return any(keyword in row for row in payments_paid)
    
async def human_option_select(page, dropdown_selector, option_text):
    dropDownList = page.locator(dropdown_selector)
    await dropDownList.select_option(option_text)
    
async def human_button_click(page, selector=None, has_text=None ,exact_text=None, check_exists=False):
    if exact_text:
        element = page.get_by_text(exact_text, exact=False)
    elif selector and has_text:
        element = page.locator(selector).filter(has_text=has_text).first
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
            response: ChatResponse = chat(model='gemma3', messages=[
            {
                'role': 'user',
                'content': 'Your task is to ONLY respond with "yes" or "no". Is there a name of a person in this description. If you see nothing, say "no":' + info,
            },
            ])
            #print(response['message']['content'])
            if operator.contains(response['message']['content'],"yes"):
                return info
            else:
                return name

    elif re.findall("^CEP ŞUBE", description):
        parts = re.split("-", description)
        info = parts[2]
        name = parts[3]
        #print("CEP",name,info.strip()+"info")
        if len(info.strip()) == 0:
            return name
        else:
            response: ChatResponse = chat(model='gemma3', messages=[
            {
                'role': 'user',
                'content': 'Your task is to ONLY respond with "yes" or "no". Is there a name of a person in this description. If you see nothing, say "no":' + info,
            },
            ])
            #print(response['message']['content'])
            if operator.contains(response['message']['content'],"yes"):
                return info
            else:
                return name
    
    return "Error 401: No name found"   


async def clean_payment_row(row_text):
    # Use LLM to clean the messy OCR row into a structured format
    response: ChatResponse = chat(model='gemma3', messages=[
        {
            'role': 'user',
            'content': 'You are a data cleaner. Extract the Payment Type, Date, Amount, and Status from this messy OCR text. \n\nRules:\n1. Output strictly a list of 4 items: [Type, Date, Amount, Status].\n2. Status must be "ÖDEDİ" or "ÖDEMEDİ".\n3. Payment Type MUST be one of these EXACT strings (fix any OCR errors to match these):\n   - "YAZILI SINAV HARCI"\n   - "UYGULAMA SINAV HARCI"\n   - "BAŞARISIZ ADAY EĞİTİMİ"\n   - "ÖZEL DERS"\n   - "BELGE ÜCRETİ"\n   - "TAKSİT"\n4. If the text contains "YZL", "SNV", "HARCI", map it to "YAZILI SINAV HARCI".\n5. If the text contains "UYG", "SNV", "HARCI", map it to "UYGULAMA SINAV HARCI".\n6. If the text contains "BASARISIZ", "ADAY", "EGITIMI", map it to "BAŞARISIZ ADAY EĞİTİMİ".\n7. If the text contains "OZEL", "DERS", map it to "ÖZEL DERS".\n8. If the text contains "BELGE", "UCRETI", map it to "BELGE ÜCRETİ".\n\nExample 1:\nInput: "ÖDEMEDİ UYG 05.12.2025 SNV. 600,00 AVUKAT HARCI"\nOutput: [UYGULAMA SINAV HARCI, 05.12.2025, 600,00, ÖDEMEDİ]\n\nExample 2:\nInput: "YZL: SNV. 05.12.2025 1.200,00 ÖDEMEDİ"\nOutput: [YAZILI SINAV HARCI, 05.12.2025, 1.200,00, ÖDEMEDİ]\n\nExample 3:\nInput: "BASARISIZ ADAY 05.12.2025 4.000,00 EĞİTİMİ"\nOutput: [BAŞARISIZ ADAY EĞİTİMİ, 05.12.2025, 4.000,00, ÖDEMEDİ]\n\nInput: ' + row_text + '\nOutput ONLY the list format like the examples.'
        },
    ])
    
    content = response['message']['content']
    # Return the first line
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    if lines:
        return lines[0]
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
async def get_payment_type(page, name_surname, payment_amount, search_new_person=True):

    #ENTER THE PERSONS PAGE AND TAKE A SCREENSHOT OF ALL PAYMENTS MADE AND PAYMENTS OWED
    if search_new_person:

        print("Clicking KURSİYER ARA...")
        await human_button_click(page, "a.btn.bg-orange", has_text="KURSİYER ARA")
        await asyncio.sleep(random.uniform(1.7, 3.7))
        
        await human_type(page, "#txtaraadi", name_surname)
        await asyncio.sleep(random.uniform(0.8, 1.8))
        await page.keyboard.press("Enter")
        await asyncio.sleep(random.uniform(1.7, 3.7))

        # Dead screen check - verify the name appears in results
        success_indicator = page.locator(f"a:has-text('{name_surname}')").first
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
                await success_indicator.wait_for(state="visible", timeout=3000)
            except:
                print(f"Both attempts failed for '{name_surname}'")
                return [["ISIM BULUNAMADI", "FLAG: 404"]]

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
    

    if payment_amount == 1200:
        if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
            print("Logic: 1200 -> YAZILI SINAV HARCI (BORC VAR)")
            payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
            return payment_types
        else: 
            payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
            return payment_types
    if payment_amount == 1600:
        if check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed):
            print("Logic: 1600 -> UYGULAMA SINAV HARCI (BORC VAR)")
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
            return payment_types
        else: 
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC YOK"])
            return payment_types
    if payment_amount == 900:
        if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
            payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
            return payment_types
        else: 
            payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
            return payment_types
    if payment_amount == 1350:
        if check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed):
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
            return payment_types
        else: 
            payment_types.append(["UYGULAMA SINAV HARCI", "BORC YOK"])
            return payment_types
    
    if payment_amount == 4000 and check_owed("BAŞARISIZ ADAY EĞİTİMİ", payment_owed):
        payment_types.append(["BAŞARISIZ ADAY EĞİTİMİ", "BORC VAR"])
        return payment_types

    if payment_amount == 4000 and check_owed("ÖZEL DERS", payment_owed):
        payment_types.append(["ÖZEL DERS", "BORC VAR"])
        return payment_types

    payment_copy = payment_amount

    if payment_amount == 2000 and check_owed("BELGE ÜCRETİ", payment_owed):
        payment_types.append(["BELGE ÜCRETİ", "BORC VAR"])
        payment_types.append(["TAKSİT", "BORC VAR"])
        return payment_types

    #COMPLEX PAYMENTS
    if payment_copy > 1600:
        if (payment_copy - 1600)%500 == 0 and payment_copy - 1600 != 4000:
            if (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed)):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            elif check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
                payment_types.append(["TAKSİT", "BORC VAR"])
        elif (payment_copy - 1600)%500 == 0 and payment_copy - 1600 == 4000:
            if (check_owed("UYG. SNV. HARCI", payment_owed) or check_owed("UYGULAMA SINAV HARCI", payment_owed)):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC VAR"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])
            elif check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["UYGULAMA SINAV HARCI", "BORC YOK"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])
        if (payment_copy - 1200)%500 == 0 and payment_copy - 1200 != 4000:
            if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                payment_types.append(["TAKSİT", "BORC VAR"])
            else:
                payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
                payment_types.append(["TAKSİT", "BORC VAR"])
        elif (payment_copy - 1200)%500 == 0 and payment_copy - 1200 == 4000:
            if check_owed("YZL. SNV. HARCI", payment_owed) or check_owed("YAZILI SINAV HARCI", payment_owed):
                payment_types.append(["YAZILI SINAV HARCI", "BORC VAR"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])
            else:
                payment_types.append(["YAZILI SINAV HARCI", "BORC YOK"])
                payment_types.append(["DORTBIN", "FLAG: 4000"])

    return payment_types


