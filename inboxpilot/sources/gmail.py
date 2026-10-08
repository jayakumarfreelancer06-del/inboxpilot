"""Gmail mode: poll unread inbox mail with the Gmail API and send approved replies in the same thread.

Setup (README → "Gmail mode"): create an OAuth client of type *Desktop app*, save it as secrets/gmail_credentials.json,
run `python -m inboxpilot.sources.gmail --authorize` once on your machine to create secrets/gmail_token.json.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import parseaddr

from inboxpilot import store
from inboxpilot.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    s = get_settings()
    creds = Credentials.from_authorized_user_file(str(s.gmail_token_file), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        s.gmail_token_file.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _plain_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(errors="replace")
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    return ""


def poll() -> int:
    svc = _service()
    s = get_settings()
    res = svc.users().messages().list(userId="me", q=s.gmail_query, maxResults=20).execute()
    n = 0
    for ref in res.get("messages", []):
        msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
        name, addr = parseaddr(headers.get("from", ""))
        added = store.add_message(
            id=f"g_{msg['id']}", source="gmail", external_id=msg["id"], thread_id=msg["threadId"], sender=addr, sender_name=name or None,
            subject=headers.get("subject"), body=_plain_body(msg["payload"]) or msg.get("snippet", ""),
        )
        if added:
            n += 1
        svc.users().messages().modify(userId="me", id=msg["id"], body={"removeLabelIds": ["UNREAD"]}).execute()
    return n


def send_reply(message: dict, subject: str, body: str, attachments: list[str]) -> str:
    svc = _service()
    original = svc.users().messages().get(userId="me", id=message["external_id"], format="metadata",
                                          metadataHeaders=["Message-ID"]).execute()
    msg_id = next((h["value"] for h in original["payload"]["headers"] if h["name"].lower() == "message-id"), None)
    mime = EmailMessage()
    mime["To"] = message["sender"]
    mime["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {message.get('subject') or subject}"
    if msg_id:
        mime["In-Reply-To"] = msg_id
        mime["References"] = msg_id
    mime.set_content(body)
    for name in attachments:
        path = get_settings().quotes_dir / name
        if path.exists():
            mime.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=name)
    raw = base64.urlsafe_b64encode(bytes(mime)).decode()
    sent = svc.users().messages().send(userId="me", body={"raw": raw, "threadId": message.get("thread_id")}).execute()
    return f"gmail: sent message {sent['id']}"


if __name__ == "__main__":
    import argparse

    from google_auth_oauthlib.flow import InstalledAppFlow

    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize", action="store_true")
    if parser.parse_args().authorize:
        s = get_settings()
        flow = InstalledAppFlow.from_client_secrets_file(str(s.gmail_credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
        s.gmail_token_file.write_text(creds.to_json())
        print(f"Saved {s.gmail_token_file}")
