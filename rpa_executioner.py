# TODO: Refactor - Merge GoldenProcessStart & GoldenUniqueProcess into ONE mother function
# TODO: Add HEADLESS config toggle
import asyncio
import random
import time
import os
import csv
import pandas as pd

from ollama import chat
from ollama import ChatResponse
from rpa_helper import human_option_select, human_button_click, human_type, get_human_name, get_payment_type, save_payment_record, update_processing_status

from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import dotenv
import easyocr
dotenv.load_dotenv()



async def golden_PaymentPaid(page, collection_type, amount):
    
    await human_button_click(page, "#btnyeniodeme")
    
    await asyncio.sleep(random.uniform(1.1, 3.7))
    
    await human_option_select(page, "#yenitahsilat_borctipi", collection_type)
    
    await asyncio.sleep(random.uniform(0.9, 3.1))
    
    await human_type(page, "#yenitahsilat_tutar", str(amount))
    
    await asyncio.sleep(random.uniform(4.1, 7.1))
    
    await human_button_click(page, "button", has_text="ÖDETTİR")
    
    await asyncio.sleep(random.uniform(1.6, 3.1))
    
   

async def golden_PaymentOwed(page, collection_type, amount):
    
    await human_button_click(page, "#btnyeniborc")
    
    await asyncio.sleep(random.uniform(1.1, 3.7))
    
    await human_option_select(page, "#yeniborc_borctipi", collection_type)
    
    await asyncio.sleep(random.uniform(0.9, 3.1))
    
    await human_type(page, "#yeniborc_tutar", str(amount))
    
    await asyncio.sleep(random.uniform(0.7, 2.1))
    
    await human_button_click(page, "button.btn-success:visible", has_text="KAYDET")
    
    await asyncio.sleep(random.uniform(1.6, 3.1))

async def RPAexecutioner_readfile(filename, sheetname):
    # Try xlrd first (for .xls), fall back to openpyxl (for .xlsx)
    try:
        dfs = pd.read_excel(filename, sheet_name=sheetname, header=14, engine='xlrd')
    except:
        dfs = pd.read_excel(filename, sheet_name=sheetname, header=14, engine='openpyxl')

    people = dfs["Açıklama"]
    payments = dfs["Tutar"]
    tag = dfs["Etiket"]
    date = dfs["Tarih"]
    bakiye = dfs["Bakiye"] if "Bakiye" in dfs.columns else None

    return [people, payments, tag, date, bakiye]


def find_starting_row_from_bakiye(bakiye_column, son_kasa_miktari):
    """
    Find the starting row by searching from bottom to top for a matching Bakiye value.
    Only compares the integer part (digits to the left of the decimal point).
    Returns the index of the matching row, or 0 if no match is found.
    """
    if bakiye_column is None or son_kasa_miktari is None or son_kasa_miktari == "":
        return 0

    try:
        # Parse the target value - only take integer part
        target = int(float(str(son_kasa_miktari).replace(',', '.').replace(' ', '')))
    except (ValueError, TypeError):
        print(f"Could not parse son_kasa_miktari: {son_kasa_miktari}")
        return 0

    # Search from bottom to top
    for i in range(len(bakiye_column) - 1, -1, -1):
        try:
            bakiye_val = bakiye_column.iloc[i]
            if pd.isna(bakiye_val):
                continue
            # Get integer part of the bakiye value
            bakiye_int = int(float(str(bakiye_val).replace(',', '.').replace(' ', '')))
            if bakiye_int == target:
                print(f"Found matching Bakiye at row {i}: {bakiye_val} (int: {bakiye_int}) == {target}")
                return i
        except (ValueError, TypeError):
            continue

    print(f"No matching Bakiye found for {son_kasa_miktari}, starting from row 0")
    return 0


async def RPAexecutioner_GoldenProcessStart(filename=None, sheetname=None, son_kasa_miktari=None):
    async with Stealth().use_async(async_playwright()) as playwright:
        chromium = playwright.chromium
        
        print("Launching browser...")
        browser = await chromium.launch(headless=False)
        
        context = await browser.new_context(ignore_https_errors=True)
        
        page = await context.new_page()
        print("Page created. Navigating to login page...")
        
        response = await page.goto("https://kurs.goldennet.com.tr/giris.php")
        
        print("Typing login credentials...")
        await human_type(page, "#kurumkodu", os.getenv("institution_code"))
        await asyncio.sleep(random.uniform(0.7, 1.9))
        
        await human_type(page, "#kullaniciadi", os.getenv("login"))
        await asyncio.sleep(random.uniform(1.1, 3.2))
        
        await human_type(page, "#kullanicisifresi", os.getenv("password"))
        await asyncio.sleep(random.uniform(0.9, 3.1))
        
        await human_button_click(page, "#btngiris")
        
        await asyncio.sleep(random.uniform(1.5, 4.1))

        # Close the notification popup
        print("Attempting to close notification popup...")
        try:
            await page.click("button.close", timeout=5000)
        except:
            print("Could not find button.close, trying text=X")
            try:
                await page.get_by_text("X", exact=True).click(timeout=2000)
            except:
                print("Could not click X either")
        await asyncio.sleep(random.uniform(1.1,2.2))


        payment_information = await RPAexecutioner_readfile(filename, sheetname)

        # Find starting row based on son_kasa_miktari if provided
        bakiye_column = payment_information[4]  # Bakiye is the 5th element

        prev_human_name = ""
        search_new_person = True
        current_cache = None

        if son_kasa_miktari:
            matched_row = find_starting_row_from_bakiye(bakiye_column, son_kasa_miktari)
            start_row = matched_row - 1
            if start_row < 0:
                print("İşlem zaten tamamlanmış - başlangıç satırı 0'ın altında.")
                await browser.close()
                if os.path.exists("payments_recorded_by_bot.csv"):
                    return pd.read_csv("payments_recorded_by_bot.csv")
                return pd.DataFrame(columns=["name", "payment_amount", "payment_type", "status"])
            print(f"Starting from row {start_row}, going backwards to 0 (Bakiye match at row {matched_row} for {son_kasa_miktari})")
            row_iterator = range(start_row, -1, -1)  # Go backwards from start_row to 0
        else:
            # No son_kasa_miktari provided, use original behavior (forward from 0)
            print(f"No Bakiye filter, processing from row 0 to {len(payment_information[0])-1}")
            row_iterator = range(len(payment_information[0]))  # Go forward from 0 to end

        for i in row_iterator:
            
            if "-" in str(payment_information[1][i]):
                print(str(payment_information[1][i]) +" Cost, not a received payment")
                continue
            if str(payment_information[2][i]) != "Para Transferi":
                print("Not a payment transfer" + str(payment_information[0][i]))
                continue

            print(f"Processing row {i}: {payment_information[0][i]}")
            name_surname = await get_human_name(str(payment_information[0][i]))
            print(f"Human name retrieved: {name_surname}")
            
            if name_surname == prev_human_name:
                search_new_person = False
            else:
                search_new_person = True
                
            if name_surname == "ERROR: 404":
                save_payment_record([name_surname, payment_information[1][i], "NA", "FLAG 404: NAME_NOT_FOUND"])
                print("Error: name not found" + str(payment_information[0][i]) + "was not attributed to any name")
                continue
            else:
                print("name found: " + name_surname)
            if name_surname == "PAYMENT_BY_POS":
                save_payment_record([name_surname, payment_information[1][i], "NA", "FLAG: POS"])
                print("Payment by pos, skipping")
                continue
            print(f"Getting payment type for {name_surname} with amount {payment_information[1][i]}")
            update_processing_status(name_surname, "processing", None, payment_information[1][i])

            payment_type, current_cache = await get_payment_type(page, name_surname,payment_information[1][i], payment_information[3][i], search_new_person, cached_data=current_cache)
            print(f"Payment type result: {payment_type}")
            
            # Sort so TAKSİT is always last
            payment_type.sort(key=lambda x: 1 if x[0] == "TAKSİT" else 0)
            
            total_paid = payment_information[1][i]
            payment_entered = 0
            for info in payment_type:
                print(f"Processing info: {info}")
                if info[1] == "FLAG: 404":
                    payment_entered = total_paid
                    save_payment_record([name_surname, payment_entered, info[0], "FLAG: 404"])
                    print("Name not found, skipping")
                if info[1] == "FLAG: 4000":
                    payment_entered = 4000
                    save_payment_record([name_surname, payment_entered, info[0], "FLAG: 4000"])
                    print("Payment amount is 4000, skipping")
                    
                if info[1] == "BORC VAR":
                    
                    if info[0] == "UYGULAMA SINAV HARCI":
                        print(f"Initiating payment: {name_surname}, {info[0]}, {1600}")
                        await golden_PaymentPaid(page, info[0], 1600)
                        print("Payment completed.")
                        await asyncio.sleep(random.uniform(2.1, 3.1))
                        if (total_paid-1600)%500 == 0:
                            payment_entered = 1600
                            total_paid -= 1600
                        else:
                            payment_entered = 1350
                            total_paid -= 1350
                    if info[0] == "YAZILI SINAV HARCI":
                        print(f"Initiating payment: {name_surname}, {info[0]}, {1200}")
                        await golden_PaymentPaid(page, info[0], 1200)
                        print("Payment completed.")
                        await asyncio.sleep(random.uniform(2.1, 3.1))
                        if (total_paid-1200)%500 == 0:
                            payment_entered = 1200
                            total_paid -= 1200
                        else:
                            payment_entered = 900
                            total_paid -= 900
                    if info[0] == "BELGE ÜCRETİ":
                        print(f"Initiating payment: {name_surname}, {info[0]}, {1000}")
                        await golden_PaymentPaid(page, info[0], 1000)
                        print("Payment completed.")
                        await asyncio.sleep(random.uniform(2.1, 3.1))
                        total_paid -= 1000
                        payment_entered = 1000
                    if info[0] == "ÖZEL DERS":
                        print(f"Initiating payment: {name_surname}, {info[0]}, {4000}")
                        await golden_PaymentPaid(page, info[0], 4000)
                        print("Payment completed.")
                        await asyncio.sleep(random.uniform(2.1, 3.1))
                        total_paid -= 4000
                        payment_entered = 1000
                    if info[0] == "BAŞARISIZ ADAY EĞİTİMİ":
                        print(f"Initiating payment: {name_surname}, {info[0]}, {4000}")
                        await golden_PaymentPaid(page, info[0], 4000)
                        print("Payment completed.")
                        await asyncio.sleep(random.uniform(2.1, 3.1))
                        total_paid -= 4000
                        payment_entered = 4000
                    if info[0] == "TAKSİT":
                        print(f"Initiating payment: {name_surname}, {info[0]}, {total_paid}")
                        await golden_PaymentPaid(page, info[0], total_paid)
                        print("Payment completed.")
                        await asyncio.sleep(random.uniform(2.1, 3.1))
                        payment_entered = total_paid
                        total_paid -= total_paid
                    update_processing_status(name_surname, "almost_completed", info[0], payment_entered)
                    save_payment_record([name_surname, payment_entered, info[0], "PAID"])
                    print("round done")
                #elif info[1] == "BORC YOK":
                #    golden_PaymentOwed(page, info[0], payment_information[1][i])
                #    golden_PaymentPaid(page, info[0], payment_information[1][i])
            
            prev_human_name = name_surname


        is_bot = await page.evaluate("navigator.webdriver")
        print(f"Am I a bot? {is_bot}")

        await browser.close()

        # Return the CSV as DataFrame for compatibility with flask_endpoint
        if os.path.exists("payments_recorded_by_bot.csv"):
            return pd.read_csv("payments_recorded_by_bot.csv")
        return pd.DataFrame(columns=["name", "payment_amount", "payment_type", "status"])

        
async def RPAexecutioner_GoldenUniqueProcess(name_surname=None, payment_type=None, payment_amount=None, is_owed=False):
    if name_surname == None or payment_type == None or payment_amount == None:
        return "Name, payment_type, or payment_amount is missing"
    async with Stealth().use_async(async_playwright()) as playwright:
        
        chromium = playwright.chromium
        
        print("Launching browser...")
        browser = await chromium.launch(headless=False)
        
        context = await browser.new_context()
        
        page = await context.new_page()
        print("Page created. Navigating to login page...")
        
        response = await page.goto("https://kurs.goldennet.com.tr/giris.php")
        
        print("Typing login credentials...")
        await human_type(page, "#kurumkodu", os.getenv("institution_code"))
        await asyncio.sleep(random.uniform(0.7, 1.9))
        
        await human_type(page, "#kullaniciadi", os.getenv("login"))
        await asyncio.sleep(random.uniform(1.1, 3.2))
        
        await human_type(page, "#kullanicisifresi", os.getenv("password"))
        await asyncio.sleep(random.uniform(0.9, 3.1))
        
        await human_button_click(page, "#btngiris")
        
        await asyncio.sleep(random.uniform(1.5, 4.1))

        # Notification popup code - commented out (no longer needed)
        # print("Attempting to close notification popup...")
        # try:
        #     await page.click("button.close", timeout=5000)
        # except:
        #     print("Could not find button.close, trying text=X")
        #     try:
        #         await page.get_by_text("X", exact=True).click(timeout=2000)
        #     except:
        #         print("Could not click X either")
        #await asyncio.sleep(random.uniform(1.1,2.2))

        print("Clicking KURSİYER ARA...")
        await human_button_click(page, "a.btn.bg-orange", has_text="KURSİYER ARA")
        
        await asyncio.sleep(random.uniform(1.7, 3.7))
        
        await human_type(page, "#txtaraadi", name_surname)

        await asyncio.sleep(random.uniform(0.8, 1.8))

        await page.keyboard.press("Enter")

        await asyncio.sleep(random.uniform(1.7, 3.7))

        await human_button_click(page, "a", has_text=name_surname)

        await asyncio.sleep(random.uniform(1.7, 3.7))

        await human_button_click(page, "a:visible", has_text="ÖDEME")
        print("in the ODEME page")

        if not is_owed:
            await golden_PaymentOwed(page, payment_type, payment_amount)
            await golden_PaymentPaid(page, payment_type, payment_amount)
        else:
            await golden_PaymentPaid(page, payment_type, payment_amount)
        
#asyncio.run(RPAexecutioner_PaymentOwed("Onur Çelik YZ Test", "TAKSİT", 6000))



# Uncomment below to test directly:
# if __name__ == "__main__":
#     print(asyncio.run(RPAexecutioner_GoldenProcessStart("belgev3.xls", "hesaphareketleri")))
