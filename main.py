import os
import subprocess
import requests
import json
from flask import Flask, request, abort
import telebot
from telebot.types import Update

BOT_TOKEN = "7188814271:AAFfuo2kc3n17fUd0hbikzdL22atM2ft3CM"
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
WEBHOOK_URL = "https://asr-robot-b0mm.onrender.com"
WEBHOOK_PATH = "/webhook"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
app = Flask(__name__)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "👋 **Ku soo dhawaaw!** Iisoo dir cod si aan qoraal ugu beddelo.")

@bot.message_handler(content_types=['voice'])
def voice_handler(message):
    status_msg = bot.reply_to(message, "⏳ **Waan guda jiraa...**")
    
    file_info = bot.get_file(message.voice.file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"

    try:
        with requests.get(file_url, stream=True) as r:
            with open("voice.ogg", "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)

        subprocess.run([
            "ffmpeg", "-y",
            "-i", "voice.ogg",
            "-ar", "16000",
            "-ac", "1",
            "voice.flac"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        url = f"https://www.google.com/speech-api/v2/recognize?client=chromium&lang=en-US&key={GOOGLE_API_KEY}"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "audio/x-flac; rate=16000"
        }

        with open("voice.flac", "rb") as f:
            response = requests.post(url, headers=headers, data=f)

        if response.status_code == 200:
            lines = response.text.splitlines()
            result_text = ""
            for line in lines:
                try:
                    data = json.loads(line)
                    if "result" in data and len(data["result"]) > 0:
                        result_text = data["result"][0]["alternative"][0]["transcript"]
                except:
                    continue
            
            if result_text:
                bot.edit_message_text(f"✅ **Natiijada:**\n\n`{result_text}`", chat_id=message.chat.id, message_id=status_msg.message_id)
            else:
                bot.edit_message_text("❌ Ma fahmin waxa aad tiri.", chat_id=message.chat.id, message_id=status_msg.message_id)
        else:
            bot.edit_message_text(f"❌ Google Error: {response.status_code}\nFadlan hubi API Key-gaaga.", chat_id=message.chat.id, message_id=status_msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Cilad: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)
    
    finally:
        if os.path.exists("voice.ogg"): os.remove("voice.ogg")
        if os.path.exists("voice.flac"): os.remove("voice.flac")

@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_data = request.get_data().decode("utf-8")
        update = Update.de_json(json_data)
        bot.process_new_updates([update])
        return "", 200
    abort(403)

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL + WEBHOOK_PATH)
    app.run(host="0.0.0.0", port=8080)
