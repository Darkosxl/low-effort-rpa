# TODO: Add /upload endpoint for Excel drag & drop
# TODO: Add /start endpoint to begin RPA processing
# TODO: Add /resolve_flag endpoint for flagged items (name/payment_type input)
from flask import Flask, request, Response, jsonify, send_file
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import requests
import json
import mimetypes
from urllib.parse import urlparse
import os
import signal
import pandas as pd
import rpa_executioner as rpaexec
from rpa_helper import infer_payment_type_from_amount, clear_processing_status
import app_paths
import threading
import multiprocessing
import asyncio
import dotenv
import sys
import logging

# Fix multiprocessing for PyInstaller on macOS
# macOS uses 'spawn' by default which doesn't work well with PyInstaller bundles
# 'fork' ensures child processes inherit parent's state (paths, imports, etc.)
if sys.platform != 'win32':
    try:
        multiprocessing.set_start_method('fork', force=True)
    except RuntimeError:
        pass  # Already set

# Required for PyInstaller multiprocessing support
multiprocessing.freeze_support()

dotenv.load_dotenv()

app = Flask(__name__)

# Disable logging for /status endpoint to reduce spam
class StatusFilter(logging.Filter):
    def filter(self, record):
        return '/status' not in record.getMessage()

log = logging.getLogger('werkzeug')
log.addFilter(StatusFilter())

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

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(__file__), relative_path)
    
def cleanup_old_files():
    """Deletes all .xls, .xlsx, and .csv files in the uploads directory."""
    try:
        uploads = app_paths.uploads_dir()
        files = os.listdir(uploads)
        for file in files:
            if file.endswith('.xls') or file.endswith('.xlsx') or file.endswith('.csv'):
                try:
                    os.remove(os.path.join(uploads, file))
                    print(f"Deleted old file: {file}")
                except Exception as e:
                    print(f"Error deleting {file}: {e}")
    except Exception as e:
        print(f"Error during cleanup: {e}")


def run_rpa_background(filename, user_phone):
    """Runs the RPA process in a background thread and sends a WhatsApp notification."""
    try:
        print(f"Starting background RPA for {filename}")
        # Run the async RPA process synchronously in this thread
        result_table = asyncio.run(rpaexec.RPAexecutioner_GoldenProcessStart(filename, sheetname="hesaphareketleri"))
        
        # Save the result for later queries (converting the CSV from RPA to Excel for the text handler)
        try:
            df = pd.read_csv(app_paths.payments_csv_path())
            df.to_excel(app_paths.result_table_path(), index=False)
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
        df = pd.read_excel(app_paths.result_table_path())
        df.at[row_index, "status"] = "PAID"
        df.at[row_index, "name"] = name
        df.at[row_index, "payment_type"] = payment_type
        df.to_excel(app_paths.result_table_path(), index=False)
        
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
        # Cleanup old files before downloading new one
        cleanup_old_files()
        
        if os.path.isfile(app_paths.result_table_path()):
            os.remove(app_paths.result_table_path())
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
            
            if not os.path.exists(app_paths.result_table_path()):
                wp_response.message("Henuz bir islem yapilmadi veya sonuc tablosu bulunamadi.")
                return Response(str(wp_response), mimetype="text/xml")

            df = pd.read_excel(app_paths.result_table_path())
            
            if (df["status"] == "PAID").all():
                os.remove(app_paths.result_table_path())
                wp_response.message("Tüm işlemler tamamlandı! Elinize sağlık Hocam! Defteri kapiyorum.")
                return Response(str(wp_response), mimetype="text/xml")
            
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + os.getenv("OPENROUTER_API_KEY"),
                },
                data=json.dumps({
                    "model": "google/gemini-3-flash-preview", 
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

@app.route("/status", methods=["GET"])
def status():
    result = {
        "payments": [],
        "current": None
    }

    if os.path.exists(app_paths.payments_csv_path()):
        try:
            df = pd.read_csv(app_paths.payments_csv_path())
            result["payments"] = df.to_dict(orient="records")
        except:
            pass
    if os.path.exists(app_paths.status_path()):
        try:
            with open(app_paths.status_path(), "r") as f:
                result["current"] = json.load(f)
        except:
            pass
    return jsonify(result)

@app.route("/whiteboard", methods=["GET"])
def whiteboard():
    return send_file(get_resource_path("whiteboard.html"))

@app.route("/logo.png", methods=["GET"])
def logo():
    return send_file(get_resource_path("public/goldenrat.png"), mimetype="image/png")

@app.route("/save-secrets", methods=["POST"])
def save_secrets():
    """Save credentials to secrets.json"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        institution_code = data.get('institution_code')
        login = data.get('login')
        password = data.get('password')

        if not institution_code or not login or not password:
            return jsonify({"error": "All fields are required"}), 400

        secrets = {
            "institution_code": institution_code,
            "login": login,
            "password": password
        }

        with open(app_paths.secrets_path(), "w") as f:
            json.dump(secrets, f, indent=2)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/load-secrets", methods=["GET"])
def load_secrets():
    """Load credentials from secrets.json"""
    try:
        if os.path.exists(app_paths.secrets_path()):
            with open(app_paths.secrets_path(), "r") as f:
                secrets = json.load(f)
            return jsonify(secrets)
        else:
            return jsonify({})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Track currently uploaded file for /start endpoint
current_uploaded_file = None
# Track running RPA process for /stop endpoint
current_rpa_process = None

@app.route("/upload", methods=["POST"])
def upload_excel():
    """Handle Excel file upload from drag & drop"""
    global current_uploaded_file

    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    # Check extension
    if not file.filename.endswith(('.xls', '.xlsx')):
        return jsonify({"error": "Only .xls and .xlsx files allowed"}), 400

    # Cleanup old files
    cleanup_old_files()
    if os.path.isfile(app_paths.result_table_path()):
        os.remove(app_paths.result_table_path())

    # Save the uploaded file to app data directory
    filename = app_paths.get_upload_path(file.filename)
    file.save(filename)
    current_uploaded_file = filename

    return jsonify({"success": True, "filename": filename})


def run_rpa_ui_process(filename, son_kasa_miktari=None):
    """Runs the RPA process (UI mode - no WhatsApp). Used by multiprocessing."""
    try:
        print(f"[UI] Starting RPA for {filename}, son_kasa_miktari={son_kasa_miktari}")
        asyncio.run(rpaexec.RPAexecutioner_GoldenProcessStart(filename, sheetname="hesaphareketleri", son_kasa_miktari=son_kasa_miktari))
        print("[UI] RPA process completed")
    except Exception as e:
        print(f"[UI] RPA failed: {e}")


@app.route("/start", methods=["POST"])
def start_rpa():
    """Start RPA processing for the uploaded Excel file"""
    global current_uploaded_file, current_rpa_process

    # Get son_kasa_miktari from request body
    son_kasa_miktari = None
    if request.is_json:
        data = request.get_json()
        son_kasa_miktari = data.get('son_kasa_miktari') if data else None

    # Check if file was uploaded
    if not current_uploaded_file or not os.path.isfile(current_uploaded_file):
        # Try to find any .xls/.xlsx file in uploads directory
        uploads = app_paths.uploads_dir()
        files = [f for f in os.listdir(uploads) if f.endswith(('.xls', '.xlsx')) and not f.startswith('result')]
        if files:
            current_uploaded_file = os.path.join(uploads, files[0])
        else:
            return jsonify({"error": "No Excel file uploaded. Please upload a file first."}), 400

    # Clean up dead process reference if exists
    if current_rpa_process and not current_rpa_process.is_alive():
        current_rpa_process = None

    # Check if RPA is already running
    if current_rpa_process and current_rpa_process.is_alive():
        return jsonify({"error": "RPA is already running. Stop it first."}), 400

    # Clear old status before starting
    clear_processing_status()

    # Start background RPA using multiprocessing (so it can be terminated)
    current_rpa_process = multiprocessing.Process(target=run_rpa_ui_process, args=(current_uploaded_file, son_kasa_miktari))
    current_rpa_process.start()

    return jsonify({"success": True, "message": f"RPA started for {current_uploaded_file}" + (f" (starting from Bakiye: {son_kasa_miktari})" if son_kasa_miktari else "")})


@app.route("/stop", methods=["POST"])
def stop_rpa():
    """Stop the running RPA process - kills browser children, process fails naturally"""
    global current_rpa_process

    if not current_rpa_process:
        current_rpa_process = None
        clear_processing_status()
        return jsonify({"success": True, "message": "State reset."})

    try:
        # Kill browser child processes - RPA will fail on its own
        pid = current_rpa_process.pid
        os.system(f"pkill -P {pid} 2>/dev/null")

        # Give it a moment to fail
        current_rpa_process.join(timeout=3)

        current_rpa_process = None
        clear_processing_status()
        return jsonify({"success": True, "message": "RPA stopped."})
    except Exception as e:
        current_rpa_process = None
        clear_processing_status()
        return jsonify({"success": True, "message": "State reset."})

@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Shutdown the application"""
    # Stop any running RPA process first
    global current_rpa_process
    if current_rpa_process and current_rpa_process.is_alive():
        try:
            pid = current_rpa_process.pid
            os.system(f"pkill -P {pid} 2>/dev/null")
            current_rpa_process.terminate()
        except:
            pass

    # Shutdown the Flask server
    os._exit(0)

if __name__ == "__main__":
    import webbrowser
    webbrowser.open("http://localhost:3987/whiteboard")
    app.run(port=3987)