"""Mock inbox: picks up .json and .eml files dropped into data/inbox/ (no external accounts needed)."""

from __future__ import annotations

import email
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from email import policy
from email.utils import parseaddr
from pathlib import Path

from inboxpilot import store
from inboxpilot.config import ROOT, get_settings

SAMPLES = ROOT / "data" / "samples"


def parse_file(path: Path) -> dict:
    if path.suffix == ".json":
        d = json.loads(path.read_text())
        name, addr = parseaddr(d["from"])
        return {"sender": addr, "sender_name": name or None, "subject": d.get("subject"), "body": d["body"],
                "source": d.get("channel", "email"), "received_at": d.get("received_at")}
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    name, addr = parseaddr(msg.get("From", ""))
    part = msg.get_body(preferencelist=("plain", "html"))
    return {"sender": addr, "sender_name": name or None, "subject": msg.get("Subject"),
            "body": part.get_content() if part else "", "source": "email", "received_at": None}


def poll() -> int:
    """Ingest new files from the inbox folder; processed files move to inbox/processed/."""
    inbox = get_settings().inbox_dir
    done = inbox / "processed"
    done.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in sorted(p for p in inbox.iterdir() if p.suffix in (".json", ".eml")):
        try:
            data = parse_file(path)
            digest = hashlib.sha1(path.name.encode() + path.read_bytes()).hexdigest()[:12]
            if store.add_message(id=f"m_{digest}", external_id=digest, **data):
                n += 1
        except Exception as exc:  # malformed file: park it, keep polling
            store.add_event(f"file:{path.name}", "ingest", "error", None, {"error": str(exc)})
        shutil.move(str(path), done / path.name)
    return n


def load_samples(names: list[str] | None = None) -> list[str]:
    """Copy demo emails into the inbox (the UI's "Drop in demo emails" button)."""
    inbox = get_settings().inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    chosen = sorted(SAMPLES.glob("*.json")) if not names else [SAMPLES / n for n in names]
    copied = []
    for src in chosen:
        if src.exists():
            # fresh timestamp + unique name, so the same sample can be dropped in again
            d = json.loads(src.read_text())
            d["received_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            target = inbox / f"{src.stem}-{uuid.uuid4().hex[:6]}.json"
            target.write_text(json.dumps(d))
            copied.append(src.name)
    return copied
