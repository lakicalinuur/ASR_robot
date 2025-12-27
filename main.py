import os
import threading
import time
import json
import base64
import logging
import requests
import shutil
from flask import Flask, request, abort
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Update

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE", "")
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook/")
WEBHOOK_URL = WEBHOOK_URL_BASE.rstrip("/") + WEBHOOK_PATH if WEBHOOK_URL_BASE else ""
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "")
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "./downloads")
GEMINI_MODELS = os.environ.get("GEMINI_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
MONGO_URI = os.environ.get("MONGO_URI", "")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

LANGS = [
("🇬🇧 English","en"), ("🇸🇦 العربية","ar"), ("🇪🇸 Español","es"), ("🇫🇷 Français","fr"),
("🇷🇺 Русский","ru"), ("🇩🇪 Deutsch","de"), ("🇮🇳 हिन्दी","hi"), ("🇮🇷 فارسی","fa"),
("🇮🇩 Indonesia","id"), ("🇺🇦 Українська","uk"), ("🇦🇿 Azərbaycan","az"), ("🇮🇹 Italiano","it"),
("🇹🇷 Türkçe","tr"), ("🇧🇬 Български","bg"), ("🇷🇸 Srpski","sr"), ("🇵🇰 اردو","ur"),
("🇹🇭 ไทย","th"), ("🇻🇳 Tiếng Việt","vi"), ("🇯🇵 日本語","ja"), ("🇰🇷 한국어","ko"),
("🇨🇳 中文","zh"), ("🇳🇱 Nederlands:nl", "nl"), ("🇸🇪 Svenska","sv"), ("🇳🇴 Norsk","no"),
("🇮🇱 עברית","he"), ("🇩🇰 Dansk","da"), ("🇪🇹 አማርኛ","am"), ("🇫🇮 Suomi","fi"),
("🇧🇩 বাংলা","bn"), ("🇰🇪 Kiswahili","sw"), ("🇪🇹 Oromo","om"), ("🇳🇵 नेपाली","ne"),
("🇵🇱 Polski","pl"), ("🇬🇷 Ελληνικά","el"), ("🇨🇿 Čeština","cs"), ("🇮🇸 Íslenska","is"),
("🇱🇹 Lietuvių","lt"), ("🇱🇻 Latviešu","lv"), ("🇭🇷 Hrvatski","hr"), ("🇷🇸 Bosanski","bs"),
("🇭🇺 Magyar","hu"), ("🇷🇴 Română","ro"), ("🇸🇴 Somali","so"), ("🇲🇾 Melayu","ms"),
("🇺🇿 O'zbekcha","uz"), ("🇵🇭 Tagalog","tl"), ("🇵🇹 Português","pt")
]

user_mode = {}
user_transcriptions = {}
user_selected_lang = {}
pending_files = {}
user_gemini_keys = {}

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
flask_app = Flask(__name__)

try:
    from pymongo import MongoClient
    client_mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) if MONGO_URI else None
    if client_mongo:
        client_mongo.admin.command("ping")
        db = client_mongo.get_default_database() if client_mongo else None
        users_col = db.get_collection("users") if db is not None else None
        if users_col is not None:
            users_col.create_index("user_id", unique=True)
            for doc in users_col.find({}, {"user_id": 1, "gemini_key": 1}):
                try:
                    user_gemini_keys[int(doc["user_id"])] = doc.get("gemini_key")
                except:
                    pass
    else:
        users_col = None
except Exception as e:
    logging.warning("Mongo init failed: %s", e)
    users_col = None

def set_user_key_db(uid, key):
    try:
        if users_col is not None:
            users_col.update_one({"user_id": uid}, {"$set": {"gemini_key": key, "updated_at": time.time()}}, upsert=True)
        user_gemini_keys[uid] = key
    except Exception as e:
        logging.warning("set_user_key_db error: %s", e)
        user_gemini_keys[uid] = key

def get_user_key_db(uid):
    if uid in user_gemini_keys:
        return user_gemini_keys[uid]
    try:
        if users_col is not None:
            doc = users_col.find_one({"user_id": uid})
            if doc:
                key = doc.get("gemini_key")
                user_gemini_keys[uid] = key
                return key
    except Exception as e:
        logging.warning("get_user_key_db error: %s", e)
    return user_gemini_keys.get(uid)

def get_user_mode(uid):
    return user_mode.get(uid, "Split messages")

class ModelRotator:
    def __init__(self, models):
        self.models = [m.strip() for m in models.split(",") if m.strip()] if isinstance(models, str) else list(models or [])
        self.pos = 0
    def get_model(self):
        if not self.models:
            return None
        model = self.models[self.pos]
        self.pos = (self.pos + 1) % len(self.models)
        return model

model_rotator = ModelRotator(GEMINI_MODELS)

def gemini_api_call(endpoint, payload, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}"
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()

def execute_with_key_and_models(action_callback, key):
    last_exc = None
    models = model_rotator.models or [None]
    for _ in range(max(1, len(models))):
        model = model_rotator.get_model()
        if not key:
            raise RuntimeError("No Gemini key configured")
        if not model:
            raise RuntimeError("No Gemini model configured")
        try:
            return action_callback(key, model)
        except Exception as e:
            last_exc = e
            logging.warning("Gemini error key=%s model=%s err=%s", str(key)[:4], model, e)
    raise RuntimeError(f"Gemini failed. Last error: {last_exc}")

def ask_gemini(text, instruction, key):
    if not key:
        raise RuntimeError("No Gemini key")
    def perform(k, model):
        payload = {"contents": [{"parts": [{"text": f"{instruction}\n\n{text}"}]}], "model": model}
        data = gemini_api_call(f"models/{model}:generateContent", payload, k)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            raise RuntimeError("Unexpected Gemini response")
    return execute_with_key_and_models(perform, key)

def transcribe_with_gemini(file_path, mime_type, key, language=None):
    if not key:
        raise RuntimeError("No Gemini key")
    with open(file_path, "rb") as f:
        file_data = f.read()
    b64_data = base64.b64encode(file_data).decode("utf-8")
    prompt = "Transcribe the audio in this file Provide a clean text that does not look like raw STT. Return ONLY the transcription text, no preamble or extra commentary."
    if language:
        prompt += f" The language is {language}."
    def perform(k, model):
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": b64_data}}
                ]
            }],
            "model": model
        }
        data = gemini_api_call(f"models/{model}:generateContent", payload, k)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            raise RuntimeError("Unexpected Gemini response during transcription")
    return execute_with_key_and_models(perform, key)

def build_action_keyboard(text_len):
    btns = []
    if text_len > 2000:
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
    return True

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if ensure_joined(message):
        welcome_text = "👋 Salaam!\n• Send me\n• voice message\n• audio file\n• video\n• to transcribe for free\n\nTo use your own Gemini key send it as a single message starting with AIz"
        kb = build_lang_keyboard("file")
        bot.reply_to(message, welcome_text, reply_markup=kb)

@bot.message_handler(func=lambda m: isinstance(m.text, str) and m.text.strip().split()[0].startswith("AIz"))
def set_key_plain(message):
    token = message.text.strip().split()[0]
    if not token.startswith("AIz"):
        return
    prev = get_user_key_db(message.from_user.id)
    set_user_key_db(message.from_user.id, token)
    msg = "API key updated." if prev else "Okay send me audio or video 👍"
    bot.reply_to(message, msg)
    if not prev and ADMIN_ID:
        try:
            uname = message.from_user.username or "N/A"
            uid = message.from_user.id
            info = f"New user provided Gemini key\nUsername: @{uname}\nId: {uid}"
            bot.send_message(ADMIN_ID, info)
        except:
            pass

@bot.message_handler(commands=['mode'])
def choose_mode(message):
    if ensure_joined(message):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
            [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
        ])
        bot.reply_to(message, "How do I send you long transcripts?:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('mode|'))
def mode_cb(call):
    mode = call.data.split("|", 1)[1]
    user_mode[call.from_user.id] = mode
    try:
        bot.edit_message_text(f"you choosed: {mode}", call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    bot.answer_callback_query(call.id, f"Mode set to: {mode} ☑️")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('lang|'))
def lang_cb(call):
    parts = call.data.split("|")
    if len(parts) < 4:
        try:
            bot.answer_callback_query(call.id, "Invalid", show_alert=True)
        except:
            pass
        return
    _, code, lbl, origin = parts
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
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except:
            pass
    chat_id = call.message.chat.id
    user_selected_lang[chat_id] = code
    try:
        bot.answer_callback_query(call.id, f"Language set: {lbl} ☑️")
    except:
        pass
    pending = pending_files.pop(chat_id, None)
    if not pending:
        return
    file_path = pending.get("path")
    mime_type = pending.get("mime")
    orig_msg = pending.get("message")
    bot.send_chat_action(chat_id, 'typing')
    user_key = get_user_key_db(call.from_user.id)
    if not user_key:
        bot.send_message(chat_id, "Send your Gemini key first (single message starting with AIz).")
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass
        return
    try:
        text = transcribe_with_gemini(file_path, mime_type, user_key, language=code)
        if not text:
            raise ValueError("Empty transcription")
        sent = send_long_text(chat_id, text, orig_msg.id, orig_msg.from_user.id)
        if sent:
            user_transcriptions.setdefault(chat_id, {})[sent.message_id] = {"text": text, "origin": orig_msg.id}
            if len(text) > 0:
                try:
                    bot.edit_message_reply_markup(chat_id, sent.message_id, reply_markup=build_action_keyboard(len(text)))
                except:
                    pass
    except Exception as e:
        try:
            bot.send_message(chat_id, "Use @MediaToTextBot 👍")
        except:
            pass
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('summarize_menu|'))
def action_cb(call):
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=build_summarize_keyboard(call.message.id))
    except:
        try:
            bot.answer_callback_query(call.id, "Opening summarize options...")
        except:
            pass

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('summopt|'))
def summopt_cb(call):
    try:
        _, style, origin = call.data.split("|")
    except:
        try:
            bot.answer_callback_query(call.id, "Invalid option", show_alert=True)
        except:
            pass
        return
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
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
        try:
            bot.answer_callback_query(call.id, "Data not found (expired). Resend file.", show_alert=True)
        except:
            pass
        return
    text = data["text"]
    bot.answer_callback_query(call.id, "Processing...")
    bot.send_chat_action(chat_id, 'typing')
    user_key = get_user_key_db(call.from_user.id)
    if not user_key:
        try:
            bot.send_message(chat_id, "Send your Gemini key first (single message starting with AIz).")
        except:
            pass
        return
    try:
        res = ask_gemini(text, prompt_instr, user_key)
        send_long_text(chat_id, res, data["origin"], call.from_user.id, log_action)
    except Exception:
        try:
            bot.send_message(chat_id, "Use @MediaToTextBot okey 😵")
        except:
            pass

def download_file_from_telegram(file_info, dest_path):
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
    with requests.get(file_url, stream=True, timeout=REQUEST_TIMEOUT) as r:
        r.raise_for_status()
        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
    return dest_path

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_media(message):
    if not ensure_joined(message):
        return
    media = message.voice or message.audio or message.video or message.document
    if not media:
        return
    if getattr(media, 'file_size', 0) > MAX_UPLOAD_SIZE:
        bot.reply_to(message, f"File too large. Please send a file smaller than {MAX_UPLOAD_MB}MB.")
        return
    file_type = "Unknown"
    mime_type = "application/octet-stream"
    if message.voice:
        file_type = "Voice"
        mime_type = "audio/ogg"
    elif message.audio:
        file_type = "Audio File"
        mime_type = getattr(message.audio, 'mime_type', 'audio/mpeg')
    elif message.video:
        file_type = "Video"
        mime_type = getattr(message.video, 'mime_type', 'video/mp4')
    elif message.document:
        file_type = f"Document ({message.document.mime_type})"
        mime_type = getattr(message.document, 'mime_type', 'application/octet-stream')
    try:
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
    except:
        pass
    bot.send_chat_action(message.chat.id, 'typing')
    ext = ""
    file_path = os.path.join(DOWNLOADS_DIR, f"temp_{message.id}_{getattr(media, 'file_unique_id', '')}")
    try:
        file_info = bot.get_file(media.file_id)
        if '.' in file_info.file_path:
            ext = file_info.file_path[file_info.file_path.rfind('.'):]
        dest_path = file_path + (ext or '')
        download_file_from_telegram(file_info, dest_path)
        lang = user_selected_lang.get(message.chat.id)
        user_key = get_user_key_db(message.from_user.id)
        if not user_key:
            pending_files[message.chat.id] = {"path": dest_path, "message": message, "mime": mime_type}
            kb = build_lang_keyboard("file")
            bot.reply_to(message, "Select the language spoken in your audio or video:", reply_markup=kb)
            return
        text = transcribe_with_gemini(dest_path, mime_type, user_key, language=lang)
        if not text:
            raise ValueError("I couldn't transcribe this file.")
        sent = send_long_text(message.chat.id, text, message.id, message.from_user.id)
        if sent:
            user_transcriptions.setdefault(message.chat.id, {})[sent.message_id] = {"text": text, "origin": message.id}
            if len(text) > 0:
                try:
                    bot.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=build_action_keyboard(len(text)))
                except:
                    pass
    except Exception:
        try:
            bot.reply_to(message, "Use @MediaToTextBot okey 😵")
        except:
            pass
        try:
            if 'dest_path' in locals() and os.path.exists(dest_path):
                os.remove(dest_path)
        except:
            pass
    finally:
        try:
            if 'dest_path' in locals() and os.path.exists(dest_path) and message.chat.id not in pending_files:
                os.remove(dest_path)
        except:
            pass

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
            sent = bot.send_document(chat_id, open(fname, 'rb'), caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
            try:
                os.remove(fname)
            except:
                pass
            return sent
    return bot.send_message(chat_id, text, reply_to_message_id=reply_id)

def _process_webhook_update(raw):
    try:
        upd = Update.de_json(raw.decode('utf-8'))
        bot.process_new_updates([upd])
    except Exception as e:
        logging.exception("Error processing update: %s", e)

@flask_app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        data = request.get_data()
        threading.Thread(target=_process_webhook_update, args=(data,), daemon=True).start()
        return '', 200
    abort(403)

@flask_app.route("/", methods=["GET"])
def index_route():
    return "Bot Running", 200

if __name__ == "__main__":
    if WEBHOOK_URL:
        try:
            bot.remove_webhook()
            time.sleep(0.5)
            bot.set_webhook(url=WEBHOOK_URL)
        except Exception as e:
            logging.error("Failed to set webhook: %s", e)
    else:
        logging.warning("WEBHOOK_URL not set; webhook will not be configured")
    flask_app.run(host="0.0.0.0", port=PORT)
