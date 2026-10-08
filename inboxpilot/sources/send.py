"""Deliver an approved reply on the channel the message came from."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from inboxpilot.config import get_settings


async def send_reply(message: dict, subject: str, body: str, attachments: list[str]) -> str:
    source = message["source"]
    if source == "gmail":
        from inboxpilot.sources import gmail

        return await asyncio.to_thread(gmail.send_reply, message, subject, body, attachments)
    if source == "whatsapp":
        from inboxpilot.sources import whatsapp

        return await asyncio.to_thread(whatsapp.send, message["sender"], body)
    return await asyncio.to_thread(_write_eml, message, subject, body, attachments)


def _write_eml(message: dict, subject: str, body: str, attachments: list[str]) -> str:
    """Mock mode: write the reply as a real .eml file to data/outbox/ instead of sending it."""
    s = get_settings()
    out_dir = s.data_dir / "outbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    msg = EmailMessage()
    msg["From"] = f"{s.business_name} <hello@brightline-supplies.example>"
    msg["To"] = message["sender"]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="brightline-supplies.example")
    msg.set_content(body)
    for name in attachments:
        path = s.quotes_dir / name
        if path.exists():
            msg.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=name)
    path = out_dir / f"{message['id']}.eml"
    path.write_bytes(bytes(msg))
    return f"mock: written to data/outbox/{path.name}"
