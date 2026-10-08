from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from inboxpilot import store
from inboxpilot.auth import User, current_user
from inboxpilot.auth import router as auth_router
from inboxpilot.config import get_settings
from inboxpilot.graph import mermaid
from inboxpilot.runtime import runtime
from inboxpilot.sources import mock, whatsapp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    yield
    await runtime.stop()


app = FastAPI(title="InboxPilot API", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "mode": s.inbox_mode, "model": s.llm_model, "llm_configured": bool(s.gemini_api_key),
            "tracing": "langfuse" if s.langfuse_public_key else "off", "whatsapp": bool(s.twilio_account_sid)}


@app.get("/api/stats")
def stats(_: User = Depends(current_user)) -> dict:
    return store.stats()


@app.get("/api/messages")
def messages(status: str | None = None, _: User = Depends(current_user)) -> list[dict]:
    return [{k: v for k, v in m.items() if k != "body"} | {"preview": m["body"][:140]} for m in store.list_messages(status)]


@app.get("/api/messages/{message_id}")
async def message(message_id: str, _: User = Depends(current_user)) -> dict:
    m = store.get(message_id)
    if m is None:
        raise HTTPException(404, "Message not found")
    return {"message": m, "state": await runtime.state(message_id), "events": store.events(message_id), "outbox": store.outbox(message_id)}


class Review(BaseModel):
    action: Literal["approve", "edit", "reject"]
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=10_000)
    note: str | None = Field(default=None, max_length=500)


@app.post("/api/messages/{message_id}/review")
async def review(message_id: str, body: Review, user: User = Depends(current_user)) -> dict:
    if body.action == "edit" and not (body.body or "").strip():
        raise HTTPException(400, "An edited reply needs a body")
    try:
        status = await runtime.review(message_id, {**body.model_dump(), "reviewer": user.email})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": status}


@app.post("/api/messages/{message_id}/retry")
def retry(message_id: str, _: User = Depends(current_user)) -> dict:
    m = store.get(message_id)
    if m is None or m["status"] != "dead_letter":
        raise HTTPException(409, "Only dead-lettered messages can be retried")
    store.update(message_id, status="queued", attempts=0, next_attempt_at=None)
    return {"status": "queued"}


class LoadSamples(BaseModel):
    names: list[str] | None = None


@app.post("/api/demo/load")
def load_samples(body: LoadSamples, _: User = Depends(current_user)) -> dict:
    return {"copied": mock.load_samples(body.names)}


@app.get("/api/demo/samples")
def samples(_: User = Depends(current_user)) -> list[str]:
    return sorted(p.name for p in mock.SAMPLES.glob("*.json"))


@app.post("/api/upload")
async def upload(files: list[UploadFile], _: User = Depends(current_user)) -> dict:
    inbox = get_settings().inbox_dir
    names = []
    for f in files:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", f.filename or "message.eml")
        if not name.endswith((".eml", ".json")):
            raise HTTPException(400, f"{f.filename}: only .eml and .json files are supported")
        (inbox / f"{uuid.uuid4().hex[:6]}-{name}").write_bytes(await f.read())
        names.append(name)
    return {"queued": names}


@app.get("/api/graph")
def graph(_: User = Depends(current_user)) -> dict:
    return {"mermaid": mermaid()}


@app.get("/api/quotes/{name}")
def quote(name: str, _: User = Depends(current_user)) -> FileResponse:
    path = get_settings().quotes_dir / name
    if not re.fullmatch(r"Q-\d+\.pdf", name) or not path.exists():
        raise HTTPException(404, "Quote not found")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/api/outbox")
def outbox(_: User = Depends(current_user)) -> list[dict]:
    return store.outbox()


@app.post("/webhooks/twilio/whatsapp")
async def twilio_whatsapp(request: Request) -> Response:
    """Inbound WhatsApp message (Twilio sandbox). Requests must carry a valid Twilio signature."""
    s = get_settings()
    form = dict(await request.form())
    url = (s.public_base_url.rstrip("/") + request.url.path) if s.public_base_url else str(request.url)
    if not whatsapp.valid_signature(url, form, request.headers.get("X-Twilio-Signature", "")):
        raise HTTPException(403, "Invalid Twilio signature")
    sid = form.get("MessageSid") or uuid.uuid4().hex
    store.add_message(id=f"w_{sid}", source="whatsapp", external_id=sid, sender=form.get("From", ""),
                      sender_name=form.get("ProfileName"), subject=None, body=form.get("Body", ""))
    return Response("<Response/>", media_type="application/xml")  # reply comes later, after human approval
