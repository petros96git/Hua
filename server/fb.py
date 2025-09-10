import os, httpx
GRAPH_URL = "https://graph.facebook.com/v18.0/me/messages"
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN","")

async def send_sender_action(recipient_id: str, action: str):
    if not FB_PAGE_TOKEN:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        params = {"access_token": FB_PAGE_TOKEN}
        payload = {"recipient":{"id":recipient_id},"sender_action":action}
        await client.post(GRAPH_URL, params=params, json=payload)

async def send_text(recipient_id: str, text: str, quick_replies=None):
    if not FB_PAGE_TOKEN:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        params = {"access_token": FB_PAGE_TOKEN}
        msg = {"text": text}
        if quick_replies:
            msg["quick_replies"] = [{"content_type":"text","title":qr["title"],"payload":qr["payload"]} for qr in quick_replies]
        payload = {"recipient":{"id":recipient_id},"message":msg}
        await client.post(GRAPH_URL, params=params, json=payload)

async def send_generic_template(recipient_id: str, elements: list):
    if not FB_PAGE_TOKEN:
        return
    attachment = {"type":"template","payload":{"template_type":"generic","elements": elements}}
    async with httpx.AsyncClient(timeout=15) as client:
        params = {"access_token": FB_PAGE_TOKEN}
        payload = {"recipient":{"id":recipient_id},"message":{"attachment": attachment}}
        await client.post(GRAPH_URL, params=params, json=payload)
