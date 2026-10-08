"""Optional WhatsApp channel via the Twilio sandbox: inbound webhook + outbound replies."""

from __future__ import annotations

from inboxpilot.config import get_settings


def valid_signature(url: str, params: dict, signature: str) -> bool:
    s = get_settings()
    if not s.twilio_auth_token:
        return False
    from twilio.request_validator import RequestValidator

    return RequestValidator(s.twilio_auth_token).validate(url, params, signature)


def send(to: str, body: str) -> str:
    s = get_settings()
    from twilio.rest import Client

    msg = Client(s.twilio_account_sid, s.twilio_auth_token).messages.create(from_=s.twilio_whatsapp_from, to=to, body=body[:1500])
    return f"whatsapp: sent {msg.sid}"
