"""
ECHO — Twilio SMS AI Accountability Bot
Database: Google Sheets (free)
AI: Groq (free)
SMS: Twilio
"""

import os
import json
import requests
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
import threading
import time
import random
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ── ENV VARIABLES ──────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID  = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")
GROQ_API_KEY        = os.environ.get("GROQ_API_KEY")
GOOGLE_SHEET_ID     = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON   = os.environ.get("GOOGLE_CREDS_JSON")  # Full JSON as string

# ── CLIENTS ─────────────────────────────────────────────────────────────────
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def get_sheet():
    """Connect to Google Sheet."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    
    # Get or create Users worksheet
    try:
        return sheet.worksheet("Users")
    except:
        ws = sheet.add_worksheet(title="Users", rows=1000, cols=5)
        ws.append_row(["Phone", "Name", "Goals", "History", "Last Updated"])
        return ws

# ── DATABASE HELPERS ─────────────────────────────────────────────────────────

def get_user(phone: str) -> dict:
    try:
        ws = get_sheet()
        records = ws.get_all_records()
        for i, row in enumerate(records):
            if row.get("Phone") == phone:
                return {"row": i + 2, **row}
        # New user — create row
        ws.append_row([phone, "", "", "[]", datetime.now().isoformat()])
        return {"Phone": phone, "Name": "", "Goals": "", "History": "[]", "row": len(records) + 2}
    except Exception as e:
        print(f"Sheet error: {e}")
        return {"Phone": phone, "Name": "", "Goals": "", "History": "[]"}

def save_user(phone: str, name: str = None, goals: str = None, history: list = None):
    try:
        ws = get_sheet()
        records = ws.get_all_records()
        for i, row in enumerate(records):
            if row.get("Phone") == phone:
                row_num = i + 2
                if name is not None:
                    ws.update_cell(row_num, 2, name)
                if goals is not None:
                    ws.update_cell(row_num, 3, goals)
                if history is not None:
                    ws.update_cell(row_num, 4, json.dumps(history[-20:]))
                ws.update_cell(row_num, 5, datetime.now().isoformat())
                return
    except Exception as e:
        print(f"Save error: {e}")

def get_history(phone: str) -> list:
    try:
        user = get_user(phone)
        history = json.loads(user.get("History", "[]"))
        return [{"role": h["role"], "content": h["content"]} for h in history[-10:]]
    except:
        return []

def append_message(phone: str, role: str, content: str):
    try:
        user = get_user(phone)
        history = json.loads(user.get("History", "[]"))
        history.append({"role": role, "content": content, "time": datetime.now().isoformat()})
        save_user(phone, history=history)
    except Exception as e:
        print(f"Append error: {e}")

# ── ECHO'S PERSONALITY ───────────────────────────────────────────────────────
ECHO_SYSTEM_PROMPT = """
You are Echo — a personal AI accountability partner that communicates via SMS.

Your personality:
- Warm, direct, slightly brutally honest best friend
- NOT a corporate chatbot — casual, human language
- Short messages only. Max 2-3 sentences. SMS style.
- No bullet points. No lists. Just natural conversation.
- Care deeply about the user's growth and time
- Sometimes challenge, sometimes encourage — read the mood
- Reference what users have told you previously

Your purpose:
- Help users reflect on how they're spending their time
- Hold them accountable to their goals
- Ask thoughtful follow-up questions
- Celebrate small wins genuinely
- Call out drift kindly but honestly

Never say you're an AI unless directly asked. If asked:
"I'm Echo — your accountability partner. Think of me as a very persistent friend."

Keep it real. Keep it short. Keep it human.
"""

CHECKIN_MESSAGES = [
    "Morning! What's the one thing you need to get done today?",
    "Hey — quick check. What are you working on right now?",
    "Afternoon check-in. How's the day going honestly?",
    "What did you actually accomplish this morning?",
    "Are you doing what you said you'd do today?",
    "What have you done in the last 2 hours? Be real with me.",
    "Evening — what went well today? What didn't?",
    "What's one thing you're proud of from today?",
    "Did you move closer to your goals today or further away?",
    "Before you wind down — what would you do differently tomorrow?",
]

# ── AI RESPONSE ──────────────────────────────────────────────────────────────

def get_echo_response(phone: str, user_message: str) -> str:
    history = get_history(phone)
    messages = [{"role": "system", "content": ECHO_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama3-8b-8192", "messages": messages, "max_tokens": 150, "temperature": 0.85},
            timeout=10
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return "Had a blip, try again in a sec?"

# ── SMS ──────────────────────────────────────────────────────────────────────

def send_sms(to: str, message: str):
    try:
        twilio_client.messages.create(body=message, from_=TWILIO_PHONE_NUMBER, to=to)
        print(f"✅ SMS sent to {to}")
    except Exception as e:
        print(f"❌ SMS error: {e}")

# ── WEBHOOK ──────────────────────────────────────────────────────────────────

@app.route("/sms", methods=["POST"])
def receive_sms():
    phone = request.form.get("From")
    user_message = request.form.get("Body", "").strip()
    print(f"📩 from {phone}: {user_message}")

    user = get_user(phone)
    history = json.loads(user.get("History", "[]"))

    # First message ever
    if len(history) == 0:
        welcome = "Hey! I'm Echo — your personal accountability partner. I'll check in on you throughout the day to keep you on track. What's your name?"
        append_message(phone, "assistant", welcome)
        send_sms(phone, welcome)
        return str(MessagingResponse())

    # Second message — they're telling us their name
    if len(history) == 1 and not user.get("Name"):
        name = user_message.split()[0].capitalize()
        save_user(phone, name=name)
        response = f"Good to meet you {name}! What's the main thing you're trying to achieve right now? A goal, a project, anything."
        append_message(phone, "user", user_message)
        append_message(phone, "assistant", response)
        send_sms(phone, response)
        return str(MessagingResponse())

    # Third message — they're sharing their goal
    if len(history) == 3 and not user.get("Goals"):
        save_user(phone, goals=user_message)

    # Normal conversation
    append_message(phone, "user", user_message)
    echo_response = get_echo_response(phone, user_message)
    append_message(phone, "assistant", echo_response)
    send_sms(phone, echo_response)

    return str(MessagingResponse())

@app.route("/health", methods=["GET"])
def health():
    return {"status": "Echo is alive 🔥", "time": datetime.now().isoformat()}, 200

# ── PROACTIVE CHECK-INS ──────────────────────────────────────────────────────

def send_daily_checkins():
    CHECKIN_HOURS = [9, 13, 17, 20]
    while True:
        now = datetime.now()
        if now.hour in CHECKIN_HOURS and now.minute == 0:
            print(f"⏰ Check-ins at {now.hour}:00")
            try:
                ws = get_sheet()
                records = ws.get_all_records()
                for row in records:
                    if row.get("Name"):
                        msg = random.choice(CHECKIN_MESSAGES)
                        send_sms(row["Phone"], msg)
                        append_message(row["Phone"], "assistant", msg)
                        time.sleep(1)
            except Exception as e:
                print(f"Check-in error: {e}")
        time.sleep(60)

checkin_thread = threading.Thread(target=send_daily_checkins, daemon=True)
checkin_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Echo starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
