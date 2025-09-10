# Webhook FastAPI για συνδεση του Facebook Messenger με το Rasa
# Υλοποιεί:
# - GET /webhook: επαλήθευση Meta κατά τη ρύθμιση της εφαρμογής
# -POST /webhook: λήψη εισερχόμενων event (messages/postbacks),εξαγωγή κειμένου, προώθηση στο Rasa REST webhook, και αποστολή απαντήσεων/template στο Messenger.
# (ολα ειναι βαση του documentation που φίνει η meta)

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
import os, httpx
from dotenv import load_dotenv
from .fb import send_sender_action, send_text, send_generic_template
from fastapi import Query

# Φόρτωση μεταβλητών περιβάλλοντος από .env (π.χ. tokens, endpoints)
load_dotenv()

# Token επαλήθευσης που δίνουμε στη Meta κατά το σετάρισμα του webhook
VERIFY_TOKEN = os.getenv("FB_VERIFY_TOKEN","")
# REST endpoint του Rasa (τοπικο για την ωρα)
RASA_REST = os.getenv("RASA_REST_ENDPOINT","http://localhost:5005/webhooks/rest/webhook")

# Δημιουργία app FastAPI
app = FastAPI(title="huahelper-webhook")

@app.get("/webhook")
async def verify_webhook(
    # Η Meta στέλνει παραμέτρους ως hub.mode, hub.challenge, hub.verify_token
    mode: str | None = Query(default=None, alias="hub.mode"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
):
    """
    Endpoint επαλήθευσης του webhook (απαιτούμενο από τηνν Meta).
    Επιστρέφουμε το challenge (text/plain) αν το verify token ταιριάζει.
    """
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge is not None:
        #Η Meta εδω περιμένει 200αρι και plain text με το challenge
        return PlainTextResponse(challenge, status_code=200)
    # Αν κατι δεν πάει καλα απορρίπτουμε με 403
    raise HTTPException(status_code=403, detail="Verification failed")

def extract_text(payload: dict) -> str:
    """
    Εξαγωγή από το αντικείμενο messaging:
      - Αν υπάρχει message.text -> επιστρέφεται αυτό
      - Αν υπάρχει quick_reply.payload -> επιστρέφεται το payload (intent?)
      - Αν υπάρχουν attachments -> «unsupported message»
      - Αν υπάρχει postback.payload -> επιστρέφεται (κουμπιά/template)
      - σε καθε αλλη περίπτωση «unsupported message»
    """
    msg = payload.get("message", {})
    if isinstance(msg, dict):
        if msg.get("text"):
            return msg["text"]
        qr = msg.get("quick_reply")
        if qr and qr.get("payload"):
            return qr["payload"]
        if msg.get("attachments"):
            return "unsupported message"
    postback = payload.get("postback")
    if postback and postback.get("payload"):
        return postback["payload"]
    return "unsupported message"

async def forward_to_rasa(sender_id: str, text: str):
    """
    Αποστολή του μηνύματος στο Rasa REST webhook.
    Επιστρέφει τη JSON λίστα απαντήσεων του Rasa (rasa_responses).
    exception σε αποτυχία HTTP.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(RASA_REST, json={"sender": sender_id, "message": text})
        r.raise_for_status()
        return r.json()

@app.post("/webhook")
async def webhook(req: Request):
    """
    Κύριο endpoint:
      - Τσεκαρει κάθε entry/messaging event
      - Στέλνει sender actions (mark_seen, typing_on) #να θυμηθω να τα ενεργοποιησω στο dash
      - Εξάγει κείμενο με extract_text
      - Καλεί Rasa
      - Για κάθε απαντηση του Rasa:
          - text -> send_text
          - buttons (quick replies) -> send_text με quick_replies
          - custom.facebook τύπου "carousel" -> send_generic_template
    """
    body = await req.json()
    for e in body.get("entry", []):
        for m in e.get("messaging", []):
            sender_id = m["sender"]["id"]
            # UX στο Messenger: είδε το μήνυμα, πληκτρολογεί…
            await send_sender_action(sender_id, "mark_seen")
            await send_sender_action(sender_id, "typing_on")

            text = extract_text(m)
            try:
                rasa_responses = await forward_to_rasa(sender_id, text)
            except Exception:
                # Μήνυμα σφάλματος αντί για «ξερό» HTTP error
                await send_text(sender_id, "Σφάλμα υπηρεσίας. Προσπάθησε ξανά αργότερα.")
                continue

            # Χαρτογράφηση απαντήσεων Rasa σε FB μηνύματα
            for r in rasa_responses:
                if "text" in r:
                    await send_text(sender_id, r["text"])
                if "buttons" in r and isinstance(r["buttons"], list):
                    # Μετατροπή Rasa buttons σε Messenger quick replies
                    qrs = [{"title": b.get("title","Επιλογή"), "payload": b.get("payload","")} for b in r["buttons"]]
                    await send_text(sender_id, r.get("text","Επιλέξτε:"), quick_replies=qrs)
                if "custom" in r and isinstance(r["custom"], dict):
                    fb = r["custom"].get("facebook")
                    # Υποστήριξη custom payload τύπου carousel
                    if isinstance(fb, dict) and fb.get("type") == "carousel":
                        await send_generic_template(sender_id, fb.get("elements", []))
    return JSONResponse({"status":"ok"})