import os
import threading
import requests
import logging
import time
import speech_recognition as sr
from pydub import AudioSegment
from flask import Flask, request, abort
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Update

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE", "")
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook/")
WEBHOOK_URL = WEBHOOK_URL_BASE.rstrip('/') + WEBHOOK_PATH if WEBHOOK_URL_BASE else ""
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "")
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "./downloads")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GEMINI_KEYS = os.environ.get("GEMINI_KEYS", GEMINI_KEY)
GEMINI_MODEL = "gemini-2.5-flash"

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class KeyRotator:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys.split(",") if k.strip()] if isinstance(keys, str) else list(keys or [])
        self.pos = 0
        self.lock = threading.Lock()
    def get_key(self):
        with self.lock:
            if not self.keys:
                return None
            key = self.keys[self.pos]
            self.pos = (self.pos + 1) % len(self.keys)
            return key
    def mark_success(self, key):
        with self.lock:
            try:
                i = self.keys.index(key)
                self.pos = (i + 1) % len(self.keys)
            except ValueError:
                pass
    def mark_failure(self, key):
        self.mark_success(key)

gemini_rotator = KeyRotator(GEMINI_KEYS)

LANGS = [
("🇬🇧 English","en-US"), ("🇸🇦 العربية","ar-SA"), ("🇪🇸 Español","es-ES"), ("🇫🇷 Français","fr-FR"),
("🇷🇺 Русский","ru-RU"), ("🇩🇪 Deutsch","de-DE"), ("🇮🇳 हिन्दी","hi-IN"), ("🇮🇷 فارسی","fa-IR"),
("🇮🇩 Indonesia","id-ID"), ("🇺🇦 Українська","uk-UA"), ("🇦🇿 Azərbaycan","az-AZ"), ("🇮🇹 Italiano","it-IT"),
("🇹🇷 Türkçe","tr-TR"), ("🇧🇬 Български","bg-BG"), ("🇷🇸 Srpski","sr-RS"), ("🇵🇰 اردو","ur-PK"),
("🇹🇭 ไทย","th-TH"), ("🇻🇳 Tiếng Việt","vi-VN"), ("🇯🇵 日本語","ja-JP"), ("🇰🇷 한국어","ko-KR"),
("🇨🇳 中文","zh-CN"), ("🇳🇱 Nederlands","nl-NL"), ("🇸🇪 Svenska","sv-SE"), ("🇳🇴 Norsk","no-NO"),
("🇮🇱 עברית","he-IL"), ("🇩🇰 Dansk","da-DK"), ("🇪🇹 አማርኛ","am-ET"), ("🇫🇮 Suomi","fi-FI"),
("🇧🇩 বাংলা","bn-BD"), ("🇰🇪 Kiswahili","sw-KE"), ("🇪🇹 Oromo","om-ET"), ("🇳🇵 नेपाली","ne-NP"),
("🇵🇱 Polski","pl-PL"), ("🇬🇷 Ελληνικά","el-GR"), ("🇨🇿 Čeština","cs-CZ"), ("🇮🇸 Íslenska","is-IS"),
("🇱🇹 Lietuvių","lt-LT"), ("🇱🇻 Latviešu","lv-LV"), ("🇭🇷 Hrvatski","hr-HR"), ("🇷🇸 Bosanski","bs-BA"),
("🇭🇺 Magyar","hu-HU"), ("🇷🇴 Română","ro-RO"), ("🇸🇴 Somali","so-SO"), ("🇲🇾 Melayu","ms-MY"),
("🇺🇿 O'zbekcha","uz-UZ"), ("🇵🇭 Tagalog","tl-PH"), ("🇵🇹 Português","pt-PT")
]

user_mode = {}
user_transcriptions = {}
user_selected_lang = {}
pending_files = {}

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
flask_app = Flask(__name__)

def get_user_mode(uid):
    return user_mode.get(uid, "Split messages")

def convert_and_transcribe(file_path, language_code):
    wav_path = file_path + ".wav"
    try:
        audio = AudioSegment.from_file(file_path)
        audio.export(wav_path, format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language=language_code)
            return text
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        raise RuntimeError(f"Google Speech API error: {e}")
    except Exception as e:
        raise RuntimeError(f"Conversion error: {e}")
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

def gemini_api_call(endpoint, payload, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}"
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()

def execute_gemini_action(action_callback):
    last_exc = None
    total = len(gemini_rotator.keys) or 1
    for _ in range(total + 1):
        key = gemini_rotator.get_key()
        if not key:
            raise RuntimeError("No Gemini keys available")
        try:
            result = action_callback(key)
            gemini_rotator.mark_success(key)
            return result
        except Exception as e:
            last_exc = e
            logging.warning(f"Gemini error with key {str(key)[:4]}: {e}")
            gemini_rotator.mark_failure(key)
    raise RuntimeError(f"Gemini failed after rotations. Last error: {last_exc}")

def ask_gemini(text, instruction):
    if not gemini_rotator.keys:
        raise RuntimeError("GEMINI_KEY(s) not configured")
    def perform(key):
        payload = {"contents": [{"parts": [{"text": f"{instruction}\n\n{text}"}]}]}
        data = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            raise RuntimeError("Unexpected Gemini response")
    return execute_gemini_action(perform)

def build_action_keyboard(text_len):
    btns = []
    if text_len > 50:
        btns.append([InlineKeyboardButton("Get Summarize", callback_data="summarize_menu|")])
    return InlineKeyboardMarkup(btns)

def build_lang_keyboard(origin):
    btns, row = [], []
    for i, (lbl, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(lbl, callback_data=f"lang|{code}|{lbl}|{origin}"))
        if i % 3 == 0:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    return InlineKeyboardMarkup(btns)

def build_summarize_keyboard(origin):
    btns = [
        [InlineKeyboardButton("Short", callback_data=f"summopt|Short|{origin}")],
        [InlineKeyboardButton("Detailed", callback_data=f"summopt|Detailed|{origin}")],
        [InlineKeyboardButton("Bulleted", callback_data=f"summopt|Bulleted|{origin}")]
    ]
    return InlineKeyboardMarkup(btns)

def ensure_joined(message):
    if not REQUIRED_CHANNEL:
        return True
    try:
        if bot.get_chat_member(REQUIRED_CHANNEL, message.from_user.id).status in ['member', 'administrator', 'creator']:
            return True
    except:
        pass
    clean = REQUIRED_CHANNEL.replace("@", "")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url=f"https://t.me/{clean}")]])
    bot.reply_to(message, "First, join my channel and come back 👍", reply_markup=kb)
    return False

def download_and_process(chat_id, file_id, lang_code, original_msg):
    try:
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_ext = os.path.splitext(file_info.file_path)[1]
        local_filename = os.path.join(DOWNLOADS_DIR, f"{file_id}{file_ext}")
        
        with open(local_filename, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        bot.send_chat_action(chat_id, 'typing')
        text = convert_and_transcribe(local_filename, lang_code)
        
        if os.path.exists(local_filename):
            os.remove(local_filename)
            
        if not text:
            bot.send_message(chat_id, "Could not transcribe audio. It might be empty or unclear.")
            return

        sent = send_long_text(chat_id, text, original_msg.id, original_msg.from_user.id)
        if sent:
            user_transcriptions.setdefault(chat_id, {})[sent.message_id] = {"text": text, "origin": original_msg.id}
            try:
                bot.edit_message_reply_markup(chat_id, sent.message_id, reply_markup=build_action_keyboard(len(text)))
            except:
                pass

    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if ensure_joined(message):
        welcome_text = (
            "👋 Salaam!\n"
            "• Send me\n"
            "• voice message\n"
            "• audio file\n"
            "• to transcribe using Google Speech"
        )
        kb = build_lang_keyboard("file")
        bot.reply_to(message, welcome_text, reply_markup=kb, parse_mode="Markdown")

@bot.message_handler(commands=['mode'])
def choose_mode(message):
    if ensure_joined(message):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
            [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
        ])
        bot.reply_to(message, "How do I send you long transcripts?:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('mode|'))
def mode_cb(call):
    if not ensure_joined(call.message):
        return
    mode = call.data.split("|")[1]
    user_mode[call.from_user.id] = mode
    try:
        bot.edit_message_text(f"you choosed: {mode}", call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    bot.answer_callback_query(call.id, f"Mode set to: {mode} ☑️")

@bot.message_handler(commands=['lang'])
def lang_command(message):
    if ensure_joined(message):
        kb = build_lang_keyboard("file")
        bot.reply_to(message, "Select the language spoken in your audio or video:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('lang|'))
def lang_cb(call):
    _, code, lbl, origin = call.data.split("|")
    if origin != "file":
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except:
            pass
        process_text_action(call, origin, f"Translate to {lbl}", f"Translate this text in to language {lbl}. No extra text ONLY return the translated text.")
        return
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    chat_id = call.message.chat.id
    user_selected_lang[chat_id] = code
    bot.answer_callback_query(call.id, f"Language set: {lbl} ☑️")
    
    pending = pending_files.pop(chat_id, None)
    if pending:
        file_id = pending.get("file_id")
        orig_msg = pending.get("message")
        download_and_process(chat_id, file_id, code, orig_msg)

@bot.callback_query_handler(func=lambda c: c.data.startswith('summarize_menu|'))
def action_cb(call):
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=build_summarize_keyboard(call.message.id))
    except:
        pass

@bot.callback_query_handler(func=lambda c: c.data.startswith('summopt|'))
def summopt_cb(call):
    try:
        _, style, origin = call.data.split("|")
    except:
        bot.answer_callback_query(call.id, "Invalid option", show_alert=True)
        return
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    prompt = ""
    if style == "Short":
        prompt = "Summarize this text in the original language in 1-2 concise sentences. No extra text — return only the summary."
    elif style == "Detailed":
        prompt = "Summarize this text in the original language in a detailed paragraph preserving key points. No extra text — return only the summary."
    else:
        prompt = "Summarize this text in the original language as a bulleted list of main points. No extra text — return only the summary."
    process_text_action(call, origin, f"Summarize ({style})", prompt)

def process_text_action(call, origin_msg_id, log_action, prompt_instr):
    chat_id = call.message.chat.id
    try:
        origin_id = int(origin_msg_id)
    except:
        origin_id = call.message.message_id
    data = user_transcriptions.get(chat_id, {}).get(origin_id)
    if not data:
        if call.message.reply_to_message:
             data = user_transcriptions.get(chat_id, {}).get(call.message.reply_to_message.message_id)
    if not data:
        bot.answer_callback_query(call.id, "Data not found. Resend file.", show_alert=True)
        return
    text = data["text"]
    bot.answer_callback_query(call.id, "Processing...")
    bot.send_chat_action(chat_id, 'typing')
    try:
        res = ask_gemini(text, prompt_instr)
        send_long_text(chat_id, res, data["origin"], call.from_user.id, log_action)
    except Exception as e:
        bot.send_message(chat_id, f"Error: {e}")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_media(message):
    if not ensure_joined(message):
        return
    media = message.voice or message.audio or message.video or message.document
    if not media:
        return
    if getattr(media, 'file_size', 0) > MAX_UPLOAD_SIZE:
        bot.reply_to(message, f"File too big. Limit is {MAX_UPLOAD_MB}MB.")
        return

    lang = user_selected_lang.get(message.chat.id)
    if not lang:
        pending_files[message.chat.id] = {"file_id": media.file_id, "message": message}
        kb = build_lang_keyboard("file")
        bot.reply_to(message, "Select the language spoken in your audio or video:", reply_markup=kb)
        return
    
    download_and_process(message.chat.id, media.file_id, lang, message)

def send_long_text(chat_id, text, reply_id, uid, action="Transcript"):
    mode = get_user_mode(uid)
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "Split messages":
            sent = None
            for i in range(0, len(text), MAX_MESSAGE_CHUNK):
                sent = bot.send_message(chat_id, text[i:i+MAX_MESSAGE_CHUNK], reply_to_message_id=reply_id)
            return sent
        else:
            fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(text)
            sent = bot.send_document(chat_id, open(fname, 'rb'), caption="Open file for full text", reply_to_message_id=reply_id)
            os.remove(fname)
            return sent
    return bot.send_message(chat_id, text, reply_to_message_id=reply_id)

@flask_app.route("/", methods=["GET"])
def index():
    return "Bot Running", 200

@flask_app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        bot.process_new_updates([Update.de_json(request.get_data().decode('utf-8'))])
        return '', 200
    abort(403)

if __name__ == "__main__":
    if WEBHOOK_URL:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(url=WEBHOOK_URL)
        flask_app.run(host="0.0.0.0", port=PORT)
    else:
        print("Webhook URL not set.")
