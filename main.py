from flask import Flask, request
import requests
import os
from io import BytesIO
from pydub import AudioSegment
import openai
import base64

app = Flask(__name__)

# Load environment variables
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")

openai.api_key = OPENAI_API_KEY

# In-memory storage for user sessions
user_sessions = {}  # phone_number -> { 'target_language': 'English', 'seen_welcome': True/False }

# Helper: send WhatsApp message
def send_whatsapp_message(to_number, text=None, audio_bytes=None, mime_type="audio/mpeg"):
    url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {"messaging_product": "whatsapp", "to": to_number}
    if text:
        data["type"] = "text"
        data["text"] = {"body": text}
    elif audio_bytes:
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        data["type"] = "audio"
        data["audio"] = {"data": audio_b64, "mime_type": mime_type}
    else:
        return
    requests.post(url, json=data, headers=headers)

# Convert audio to WAV
def convert_to_wav(audio_bytes):
    audio = AudioSegment.from_file(BytesIO(audio_bytes))
    buf = BytesIO()
    audio.export(buf, format="wav")
    buf.seek(0)
    return buf

# Transcribe audio using OpenAI Whisper
def transcribe_audio(audio_bytes):
    wav_file = convert_to_wav(audio_bytes)
    transcript = openai.Audio.transcriptions.create(
        model="whisper-1",
        file=wav_file
    )
    return transcript["text"]

# Translate text to target language
def translate_text(text, target_language):
    prompt = f"Translate the following text to {target_language}:\n{text}"
    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}]
    )
    return response["choices"][0]["message"]["content"]

# Text-to-speech (TTS)
def text_to_speech(text, voice="alloy"):
    audio_resp = openai.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice=voice,
        input=text
    )
    return audio_resp

# Webhook route
@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method == "GET":
        # Verification for Meta
        verify_token = "your_verify_token_here"  # You will set this in Meta dashboard
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == verify_token:
            return challenge, 200
        return "Verification failed", 403

    data = request.json
    try:
        for entry in data.get("entry", []):
            for message_event in entry.get("changes", []):
                value = message_event.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    from_number = msg["from"]

                    # Send welcome message if new user
                    if from_number not in user_sessions:
                        user_sessions[from_number] = {"target_language":"English", "seen_welcome":True}
                        welcome_text = (
                            "Hi! Welcome to the Translator Bot 🌐\n"
                            "1️⃣ Send your target language using: /translate_to <Language>\n"
                            "2️⃣ Then send your voice note in any language.\n"
                            "The bot will reply with translated audio in your chosen language."
                        )
                        send_whatsapp_message(from_number, welcome_text)

                    # Check for target language command
                    if "text" in msg:
                        txt = msg["text"]["body"]
                        if txt.lower().startswith("/translate_to"):
                            parts = txt.split(" ", 1)
                            if len(parts) == 2:
                                lang = parts[1].strip()
                                user_sessions[from_number]["target_language"] = lang
                                send_whatsapp_message(from_number, f"Target language set to {lang} ✅")
                            continue

                    # Process audio messages
                    if "audio" in msg:
                        media_id = msg["audio"]["id"]
                        # Get media URL from WhatsApp
                        media_url_resp = requests.get(
                            f"https://graph.facebook.com/v17.0/{media_id}",
                            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
                        )
                        media_url = media_url_resp.json()["url"]
                        audio_resp = requests.get(
                            media_url,
                            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
                        )
                        audio_bytes = audio_resp.content

                        # Transcribe and translate
                        text = transcribe_audio(audio_bytes)
                        target_lang = user_sessions[from_number]["target_language"]
                        translated_text = translate_text(text, target_lang)

                        # Convert translated text to speech
                        tts_audio = text_to_speech(translated_text)
                        send_whatsapp_message(from_number, audio_bytes=tts_audio)

        return "EVENT_RECEIVED", 200
    except Exception as e:
        print(e)
        return "ERROR", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
