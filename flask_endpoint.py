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
from rpa_helper import infer_payment_type_from_amount
import threading
import asyncio
import dotenv

dotenv.load_dotenv()

app = Flask(__name__)

SYSTEM_PROMPT = """
You are a data extraction assistant for a driving school.
Allowed Payment Categories: [YAZILI SINAV HARCI, UYGULAMA SINAV HARCI, ÖZEL DERS, BAŞARISIZ ADAY EĞİTİMİ, BELGE ÜCRETİ, TAKSİT].

Rules:
1. Extract the full name AND payment type from the message.
2. Map payment descriptions to the allowed categories ONLY.
3. Output strictly in JSON format:
   {"name": "Full Name or null", "payment_type": "CATEGORY or null"}
4. If the message is useless or contains no relevant info, output:
   {"no_information": "reason"}
5. Do not output markdown, code blocks, or explanation. Just raw JSON.
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
            message_body = "Onur Bey Son tarama sonuclarinizi bulabilirsiniz: \n" + str(result_table) + "\n hocam lutfen ilk PAID olmayan satirin adini ve odemesini belirtir misiniz?"
            
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


def run_unique_process_background(name, payment_type, payment_amount, row_index, user_phone):
    """Runs the unique RPA process in background and sends notification when done."""
    try:
        print(f"Starting unique process for {name} - {payment_type}")
        asyncio.run(rpaexec.RPAexecutioner_GoldenUniqueProcess(
            name_surname=name,
            payment_type=payment_type,
            payment_amount=payment_amount,
            is_owed=True
        ))
        
        # Update the Excel file
        df = pd.read_excel("result_table.xlsx")
        df.at[row_index, "status"] = "PAID"
        df.at[row_index, "name"] = name
        df.at[row_index, "payment_type"] = payment_type
        df.to_excel("result_table.xlsx", index=False)
        
        # Send success notification
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        from_number = os.getenv('TWILIO_PHONE_NUMBER')
        
        if account_sid and auth_token and from_number:
            Client(account_sid, auth_token).messages.create(
                from_=from_number,
                body=f"✅ {name} - {payment_type} - {payment_amount} Golden'a giris yapildi!",
                to=user_phone
            )
            print("Unique process notification sent.")
        
    except Exception as e:
        print(f"Unique process failed: {e}")
        try:
            account_sid = os.getenv('TWILIO_ACCOUNT_SID')
            auth_token = os.getenv('TWILIO_AUTH_TOKEN')
            from_number = os.getenv('TWILIO_PHONE_NUMBER')
            if account_sid and auth_token and from_number:
                Client(account_sid, auth_token).messages.create(
                    from_=from_number,
                    body=f"❌ Hata: {name} icin islem yapilamadi: {str(e)}",
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
        if os.path.isfile("result_table.xlsx"):
            os.remove("result_table.xlsx")
        media_url = request.form.get("MediaUrl0")
        content_type = request.form.get(f'MediaContentType{0}')
        ext = mimetypes.guess_extension(content_type) or '.bin'
        
        media_sid = os.path.basename(urlparse(media_url).path)
        filename = f"{media_sid}{ext}"
        
        # Twilio media URLs require authentication
        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        
        if not account_sid or not auth_token:
            print("Error: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN missing in .env")
            wp_response.message("Sunucu hatasi: Twilio kimlik bilgileri eksik.")
            return Response(str(wp_response), mimetype="text/xml")
        
        print(f"Downloading media from {media_url}...")
        r = requests.get(media_url, auth=(account_sid, auth_token))
        
        if r.status_code != 200:
            print(f"Error downloading media: Status {r.status_code}")
            print(f"Response: {r.text[:200]}") # Print first 200 chars of error
            wp_response.message("Dosya indirilemedi. Lutfen tekrar deneyin.")
            return Response(str(wp_response), mimetype="text/xml")

        with open(filename, 'wb') as f:
            f.write(r.content)

        # Start background thread
        thread = threading.Thread(target=run_rpa_background, args=(filename, sender))
        thread.start()

        wp_response.message("Dosya alindi, islem baslatiliyor. Lutfen bekleyiniz...")
        return Response(str(wp_response), mimetype="text/xml")

    # Answering questions
    else:
        # After updating the status to PAID, check if all are done
        
        try:
            
            if not os.path.exists("result_table.xlsx"):
                wp_response.message("Henuz bir islem yapilmadi veya sonuc tablosu bulunamadi.")
                return Response(str(wp_response), mimetype="text/xml")

            df = pd.read_excel("result_table.xlsx")
            
            if (df["status"] == "PAID").all():
                os.remove("result_table.xlsx")
                wp_response.message("Tüm işlemler tamamlandı! Elinize sağlık Hocam! Defteri kapiyorum.")
                return Response(str(wp_response), mimetype="text/xml")
            
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
            
            # Parse LLM JSON response
            try:
                llm_data = json.loads(llm_output.strip())
            except:
                llm_data = {}
            
            if "no_information" in llm_data:
                wp_response.message("Anlasilmadi veya islem gerektirmiyor: " + message)
                return Response(str(wp_response), mimetype="text/xml")
            
            # Find first row that needs attention (not PAID)
            for i in range(len(df)):
                status = str(df["status"][i])
                if status == "PAID":
                    continue
                    
                # Get row data
                name = str(df["name"][i])
                payment_type = str(df["payment_type"][i])
                payment_amount = df["payment_amount"][i]
                
                # If name is missing (FLAG: 404 means name search failed)
                if "BULUNAMADI" in name or "404" in status:
                    name = llm_data.get("name") or name
                
                # If payment type is ambiguous (DORTBIN or FLAG: 4000)
                if "DORTBIN" in payment_type:
                    payment_type = llm_data.get("payment_type") or payment_type
                else:
                    payment_type = infer_payment_type_from_amount(payment_amount)

                # Start background thread for RPA processing
                print(name, payment_type, payment_amount, i, sender)
                thread = threading.Thread(
                    target=run_unique_process_background,
                    args=(name, payment_type, payment_amount, i, sender)
                )
                thread.start()
                
                wp_response.message(f"Islem baslatildi: {name} - {payment_type}. Lutfen bekleyiniz...")
                return Response(str(wp_response), mimetype="text/xml")
            

        except Exception as e:
            print(f"Error processing text message: {e}")
            wp_response.message("Bir hata olustu: " + str(e))

        return Response(str(wp_response), mimetype="text/xml")

@app.route("/health", methods=["GET"])
def health():
    return "200 OK"

if __name__ == "__main__":
    app.run(port=3987)