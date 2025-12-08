from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
import requests
import json
import mimetypes
from urllib.parse import urlparse
import os
import pandas as pd
import rpa_executioner as rpaexec

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

@app.route("/reply_whatsapp", methods=["POST"])
def reply_whatsapp():
    message = request.form.get("Body")
    num_media = int(requests.form.get("NumMedia", 0))
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


        try:
            
            result_table = await rpaexec.RPAexecutioner_GoldenProcessStart(filename, sheetname="hesaphareketleri")
            wp_response.message("Onur Bey Son tarama sonuclarinizi bulabilirsiniz: \n" + result_table + "\n lutfen sirayla belirtin")
            df.to_excel("result_table.xlsx", index=False)            
            return Response(str(wp_response), mimetype="text/xml")
        except Exception as e:
            print(f"Failed to read Excel: {e}")
            wp_response.message("Excel okunamadi: " + str(e))
            return Response(str(wp_response), mimetype="text/xml")
    #answering to questions
    else:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer <> " + os.getenv("OPENROUTER_API_KEY"),
            },
            data=json.dumps({
                "model": "google/gemini-2.5-flash", # Optional
                "messages": [
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': "Analyze this message: " + message}
                ]
            })
        )
        
        llm_output = response['message']['content']

        if "no_information" in llm_output:
            wp_response.message("bu mesaji esgeciyorum: " + message)
        elif "name" in llm_output:  

            wp_response.message(llm_output + " adi ogrenilen Vatandasin odemesi Golden'a giris yapildi: ")
        elif "payment_type" in llm_output:

            wp_response.message( + "Vatandasin ogrenilen odemesi Golden'a giris yapildi: " + llm_output)

    return Response(str(wp_response), mimetype="text/xml")

if __name__ == "__main__":
    app.run(port=3987)