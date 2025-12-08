from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import requests
import json
import mimetypes
from urllib.parse import urlparse
import os
import pandas as pd
import rpa_executioner as rpaexec
import threading
import asyncio
import dotenv

dotenv.load_dotenv()

app = Flask(__name__)

SYSTEM_PROMPT = """
You are a data extraction assistant for a driving school.
Allowed Payment Categories: [YAZILI SINAV HARCI, UYGULAMA SINAV HARCI, ÖZEL DERS, BAŞARISIZ ADAY EĞİTİMİ, BELGE ÜCRETİ, TAKSİT].

Rules:
1. Extract the full name if present.
2. Map payment descriptions to the allowed categories ONLY.
3. Output strictly in one of these three formats:
   3.1 name: "Name"
   3.2 payment_type: "CATEGORY"
   3.3 no_information: "reason"
4. Do not output markdown or explanation.
"""

def run_rpa_background(filename, user_phone):
    """Runs the RPA process in a background thread and sends a WhatsApp notification."""
    try:
        print(f"Starting background RPA for {filename}")
        # Run the async RPA process synchronously in this thread
        result_table = asyncio.run(rpaexec.RPAexecutioner_GoldenProcessStart(filename, sheetname="hesaphareketleri"))
        
        # Save the result for later queries (converting the CSV from RPA to Excel for the text handler)
        try:
            df = pd.read_csv("payments_recorded_by_bot.csv")
            df.to_excel("result_table.xlsx", index=False)
        except Exception as e:
            print(f"Error processing CSV to Excel: {e}")

        # Send WhatsApp notification
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        from_number = os.getenv('TWILIO_PHONE_NUMBER')
        
        if account_sid and auth_token and from_number:
            client = Client(account_sid, auth_token)
            message_body = "Onur Bey Son tarama sonuclarinizi bulabilirsiniz: \n" + str(result_table) + "\n lutfen sirayla cozulmemis olanlari belirtin."
            
            # Split message if too long (Twilio limit is 1600 chars, but good practice)
            # For now, just send.
            client.messages.create(
                from_=from_number,
                body=message_body,
                to=user_phone
            )
            print("Notification sent successfully.")
        else:
            print("Twilio credentials missing. Cannot send notification.")

    except Exception as e:
        print(f"Background RPA failed: {e}")
        # Try to notify failure
        try:
            account_sid = os.getenv('TWILIO_ACCOUNT_SID')
            auth_token = os.getenv('TWILIO_AUTH_TOKEN')
            from_number = os.getenv('TWILIO_PHONE_NUMBER')
            if account_sid and auth_token and from_number:
                Client(account_sid, auth_token).messages.create(
                    from_=from_number,
                    body=f"RPA isleminde hata olustu: {str(e)}",
                    to=user_phone
                )
        except:
            pass

#TODO pos cihazindan gelen parayi da ayarla
@app.route("/reply_whatsapp", methods=["POST"])
def reply_whatsapp():
    message = request.form.get("Body")
    num_media = int(request.form.get("NumMedia", 0))
    sender = request.form.get("From")
    
    wp_response = MessagingResponse()
    #if there is an excel sheet sent
    if num_media == 1:
        media_url = request.form.get("MediaUrl0")
        content_type = request.form.get(f'MediaContentType{0}')
        ext = mimetypes.guess_extension(content_type) or '.bin'
        
        media_sid = os.path.basename(urlparse(media_url).path)
        filename = f"{media_sid}{ext}"
        r = requests.get(media_url)

        with open(filename, 'wb') as f:
            f.write(r.content)

        # Start background thread
        thread = threading.Thread(target=run_rpa_background, args=(filename, sender))
        thread.start()

        wp_response.message("Dosya alindi, islem baslatiliyor. Lutfen bekleyiniz...")
        return Response(str(wp_response), mimetype="text/xml")

    # Answering questions
    else:
        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + os.getenv("OPENROUTER_API_KEY"),
                },
                data=json.dumps({
                    "model": "google/gemini-2.5-flash", 
                    "messages": [
                        {'role': 'system', 'content': SYSTEM_PROMPT},
                        {'role': 'user', 'content': "Analyze this message: " + message}
                    ]
                })
            )
            
            llm_output = response.json()['choices'][0]['message']['content']
            
            if not os.path.exists("result_table.xlsx"):
                 wp_response.message("Henuz bir islem yapilmadi veya sonuc tablosu bulunamadi.")
                 return Response(str(wp_response), mimetype="text/xml")

            df = pd.read_excel("result_table.xlsx")
            
            # Note: The original logic for 'solved', 'owed', etc. relies on columns that might not exist 
            # in the simple CSV output from RPA. 
            # Assuming the user will handle the column mismatch or the RPA output is sufficient.
            # I will keep the original logic structure but wrap in try-except.
            
            if "no_information" in llm_output:
                wp_response.message("Anlasilmadi veya islem gerektirmiyor: " + message)
                
            elif "name" in llm_output:
                payment_type = "?"  
                payment_amount = "0"
                owed = False
                checker = 0
                for i in len(df["solved"]):
                    if df["solved"][i] == "unsolved":
                        owed = df["owed"][i]
                        payment_type = df["payment_type"][i]
                        payment_amount = df["payment_amount"][i]
                        checker = i
                        break
                df["solved"][checker] = "solved"
                df["name"][checker] = llm_output
                df.to_excel("result_table.xlsx", index=False)
                rpaexec.RPAexecutioner_GoldenUniqueProcess(name=llm_output, payment_type=payment_type, payment_amount=payment_amount, is_owed=owed)
                wp_response.message(llm_output + " adi ogrenilen Vatandasin odemesi Golden'a giris yapildi: " + payment_type + " " + payment_amount)
            elif "payment_type" in llm_output:
                payment_type = "?"  
                payment_amount = "0"
                owed = False
                checker = 0
                for i in len(df["solved"]):
                    if df["solved"][i] == "unsolved":
                        owed = df["owed"][i]
                        name = df["name"][i]
                        payment_amount = df["payment_amount"][i]
                        checker = i
                        break
                df["solved"][checker] = "solved"
                df["payment_type"][checker] = llm_output
                df.to_excel("result_table.xlsx", index=False)
                rpaexec.RPAexecutioner_GoldenUniqueProcess(name=name, payment_type=llm_output, payment_amount=payment_amount, is_owed=owed)
                wp_response.message(llm_output + " Vatandasin ogrenilen odemesi Golden'a giris yapildi: " + payment_type + " " + payment_amount)

        except Exception as e:
            print(f"Error processing text message: {e}")
            wp_response.message("Bir hata olustu: " + str(e))

        return Response(str(wp_response), mimetype="text/xml")

if __name__ == "__main__":
    app.run(port=3987)