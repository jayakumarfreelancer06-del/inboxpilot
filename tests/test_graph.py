"""End-to-end graph runs with a fake LLM: routing, human-in-the-loop, restarts and the dead-letter queue."""

import itertools

import pytest

from inboxpilot import graph, store
from inboxpilot.config import get_settings
from inboxpilot.runtime import Runtime
from inboxpilot.schemas import Classification, Complaint, DraftReply, QuoteItem, QuoteRequest

_ids = itertools.count()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def fake_llm(monkeypatch, intent="quote_request", confidence=0.95, fail_times=0):
    calls = {"classify": 0}

    async def classify_message(message, config=None):
        calls["classify"] += 1
        if calls["classify"] <= fail_times:
            raise RuntimeError("simulated LLM outage")
        return Classification(intent=intent, confidence=confidence, reason="test")

    async def extract_fields(intent_, message, config=None):
        if intent_ == "quote_request":
            return QuoteRequest(items=[QuoteItem(product="medium boxes", quantity=2000)], company="NorthPeak", delivery_date="2026-10-24")
        return Complaint(order_number="SO-10470", issue_type="damaged", severity="high", requested_resolution="replacement", summary="Crushed boxes")

    async def draft_reply(state, config=None):
        return DraftReply(subject="Re: your request", body=f"Draft for {state['classification']['intent']}", internal_note="check")

    monkeypatch.setattr(graph, "classify_message", classify_message)
    monkeypatch.setattr(graph, "extract_fields", extract_fields)
    monkeypatch.setattr(graph, "draft_reply", draft_reply)
    monkeypatch.setattr(graph, "RETRY", graph.RetryPolicy(max_attempts=1))
    return calls


def new_message(sender="maya.chen@northpeak-outdoor.com") -> dict:
    mid = f"m_test{next(_ids)}"
    store.add_message(id=mid, source="email", sender=sender, subject="Hello", body="Please quote 2000 medium boxes")
    return store.get(mid)


async def started() -> Runtime:
    rt = Runtime()
    await rt.start(background=False)
    return rt


async def test_quote_request_pauses_for_review_then_sends_with_pdf(monkeypatch):
    fake_llm(monkeypatch)
    rt = await started()
    m = new_message()
    assert await rt.process(m) == "awaiting_review"
    state = await rt.state(m["id"])
    assert state["waiting_for_review"] and state["facts"]["quote"]["created"]
    assert not store.outbox(m["id"])  # nothing is sent before approval

    assert await rt.review(m["id"], {"action": "approve", "reviewer": "me@example.com"}) == "sent"
    sent = store.outbox(m["id"])[0]
    assert sent["attachments"] == [state["facts"]["quote"]["pdf"]] and not sent["edited"]
    assert (get_settings().data_dir / "outbox" / f"{m['id']}.eml").exists()
    steps = [e["step"] for e in store.events(m["id"])]
    assert steps == ["classify", "extract", "act", "draft", "human_review", "human_review", "send"]
    await rt.stop()


async def test_edit_and_reject(monkeypatch):
    fake_llm(monkeypatch, intent="complaint")
    rt = await started()
    a, b = new_message("omar@cedarandsage.com"), new_message("omar@cedarandsage.com")
    await rt.process(a)
    await rt.process(b)
    assert await rt.review(a["id"], {"action": "edit", "body": "Edited reply", "reviewer": "me"}) == "sent"
    assert store.outbox(a["id"])[0]["body"] == "Edited reply" and store.outbox(a["id"])[0]["edited"]
    assert await rt.review(b["id"], {"action": "reject", "reviewer": "me"}) == "rejected"
    assert store.outbox(b["id"]) == []
    with pytest.raises(ValueError):
        await rt.review(b["id"], {"action": "approve"})
    await rt.stop()


async def test_low_confidence_goes_straight_to_a_human(monkeypatch):
    fake_llm(monkeypatch, confidence=0.4)
    rt = await started()
    m = new_message()
    assert await rt.process(m) == "awaiting_review"
    state = await rt.state(m["id"])
    assert state["needs_human"] and "draft" not in state and "extraction" not in state
    assert await rt.review(m["id"], {"action": "edit", "body": "Written by a human", "reviewer": "me"}) == "sent"
    await rt.stop()


async def test_confident_spam_is_archived_without_reply(monkeypatch):
    fake_llm(monkeypatch, intent="spam_other")
    rt = await started()
    m = new_message()
    assert await rt.process(m) == "archived"
    assert store.outbox(m["id"]) == []
    await rt.stop()


async def test_paused_run_survives_restart(monkeypatch):
    fake_llm(monkeypatch)
    rt = await started()
    m = new_message()
    await rt.process(m)
    await rt.stop()

    rt2 = await started()  # new process, same checkpoint database
    assert (await rt2.state(m["id"]))["waiting_for_review"]
    assert await rt2.review(m["id"], {"action": "approve", "reviewer": "me"}) == "sent"
    await rt2.stop()


async def test_failures_back_off_then_dead_letter(monkeypatch):
    calls = fake_llm(monkeypatch, fail_times=99)
    rt = await started()
    m = new_message()
    assert await rt.process(m) == "queued"
    first = store.get(m["id"])
    assert first["next_attempt_at"] and "simulated LLM outage" in first["last_error"]
    assert await rt.process(store.get(m["id"])) == "queued"
    assert await rt.process(store.get(m["id"])) == "dead_letter"
    assert calls["classify"] == 3


async def test_recovers_after_transient_failure(monkeypatch):
    fake_llm(monkeypatch, fail_times=1)
    rt = await started()
    m = new_message()
    assert await rt.process(m) == "queued"
    assert await rt.process(store.get(m["id"])) == "awaiting_review"
    await rt.stop()
