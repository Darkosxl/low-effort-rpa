import asyncio
import random
import time
import os

from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import dotenv

dotenv.load_dotenv()

async def human_option_select(page, dropdown_selector, option_text):
    dropDownList = page.locator(dropdown_selector)
    await dropDownList.select_option(option_text)
    
async def human_button_click(page, selector=None, has_text=None ,exact_text=None, check_exists=False):
    if exact_text:
        element = page.get_by_text(exact_text, exact=False)
    elif has_text:
        element = page.locator(selector, has_text=has_text).first
    elif selector:
        element = page.locator(selector).first
    else:
        console.log("No selector provided")
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
    
async def RPAexecutioner_Fill(name_surname, collection_type, amount):
    async with Stealth().use_async(async_playwright()) as playwright:
        chromium = playwright.chromium
        
        browser = await chromium.launch(headless=False)
        
        context = await browser.new_context()
        
        page = await context.new_page()
        
        response = await page.goto("https://kurs.goldennet.com.tr/giris.php")
        
        await human_type(page, "#kurumkodu", os.getenv("institution_code"))
        await asyncio.sleep(random.uniform(0.7, 1.9))
        
        await human_type(page, "#kullaniciadi", os.getenv("login"))
        await asyncio.sleep(random.uniform(1.1, 3.2))
        
        await human_type(page, "#kullanicisifresi", os.getenv("password"))
        await asyncio.sleep(random.uniform(0.9, 3.1))
        
        await human_button_click(page, "#btngiris")
        
        await asyncio.sleep(random.uniform(1.5, 4.1))
        
        await human_button_click(page, "a.btn.bg-orange", has_text="KURSİYER ARA")
        
        await asyncio.sleep(random.uniform(1.7, 3.7))
        
        await human_type(page, "#txtaraadi", name_surname)
        
        await asyncio.sleep(random.uniform(0.8, 1.8))
        
        await page.keyboard.press("Enter")
        
        await asyncio.sleep(random.uniform(1.7, 3.7))
        
        await human_button_click(page, "a", has_text=name_surname)
        
        await asyncio.sleep(random.uniform(1.7, 3.7))
        
        await human_button_click(page, "a:visible", has_text="ÖDEME")

        await asyncio.sleep(random.uniform(0.8, 1.8))
        
        await human_button_click(page, "#btnyeniodeme")
        
        await asyncio.sleep(random.uniform(1.1, 3.7))
        
        await human_option_select(page, "#yenitahsilat_borctipi", collection_type)
        
        await asyncio.sleep(random.uniform(0.9, 3.1))
        
        await human_type(page, "#yenitahsilat_tutar", str(amount))
        
        await asyncio.sleep(random.uniform(0.7, 2.1))
        
        await human_button_click(page, "button", has_text="ÖDETTİR")
        
        await asyncio.sleep(random.uniform(1.6, 3.1))
        
        is_bot = await page.evaluate("navigator.webdriver")
        
        print(f"Am I a bot? {is_bot}")
                
        
        await browser.close()
        
    
asyncio.run(RPAexecutioner_Fill("ANIL KUŞ", "UYGULAMA SINAV HARCI", 1200))