"""The InboxPilot LangGraph state graph.

    classify ──(spam, confident)──────────────────────────────► archive
       │ └──(confidence < threshold)──────────────► human_review
       ▼                                                ▲   │
    extract ─► act (ERP tools) ─► draft ────────────────┘   ├─ approve / edit ─► send
                                                            └─ reject ─────────► END

Every node that calls the LLM or a tool has a RetryPolicy (exponential backoff + jitter).
human_review uses `interrupt`, so the run pauses until a person approves, edits or rejects;
state is checkpointed in SQLite, so paused and half-finished runs survive restarts.
"""

from __future__ import annotations

import time
from functools import wraps
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt

from inboxpilot import erp, store
from inboxpilot.config import get_settings
from inboxpilot.llm import chat_model
from inboxpilot.schemas import EXTRACTION_SCHEMAS, Classification, DraftReply
from inboxpilot.sources.send import send_reply


class State(TypedDict, total=False):
    message: dict
    classification: dict
    extraction: dict
    facts: dict
    attachments: list[str]
    draft: dict
    needs_human: bool
    human_reason: str
    review: dict
    outcome: Literal["sent", "rejected", "archived"]
    delivery: str


RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0, jitter=True)


def audited(step: str):
    """Record each node run (duration + short summary) in the message's audit trail."""

    def deco(fn):
        @wraps(fn)
        async def wrapper(state: State, config: RunnableConfig) -> dict:
            mid = state["message"]["id"]
            start = time.perf_counter()
            try:
                out = await fn(state, config)
            except Exception as exc:
                store.add_event(mid, step, "error", int((time.perf_counter() - start) * 1000), {"error": f"{type(exc).__name__}: {exc}"[:300]})
                raise
            store.add_event(mid, step, "ok", int((time.perf_counter() - start) * 1000), _summary(out))
            return out

        return wrapper

    return deco


def _summary(out: dict) -> dict:
    keep = {}
    for k, v in out.items():
        if k == "message":
            continue
        keep[k] = v if len(str(v)) < 1500 else str(v)[:1500] + "…"
    return keep


def _email_text(m: dict) -> str:
    who = f"{m.get('sender_name') or ''} <{m['sender']}>".strip()
    return f"Channel: {m['source']}\nFrom: {who}\nSubject: {m.get('subject') or '(none)'}\n\n{m['body']}"


# ------------------------------------------------------------------------------ nodes
CLASSIFY_SYSTEM = """You triage the shared inbox of {business}, a B2B packaging supplier (boxes, tape, bubble wrap, pallets, labels).
Classify the message into exactly one intent. Be honest about confidence: emails that mix several requests, are very vague,
or could reasonably be two intents get a confidence below 0.7."""


async def classify_message(message: dict, config: RunnableConfig | None = None) -> Classification:
    llm = chat_model().with_structured_output(Classification)
    return await llm.ainvoke(
        [SystemMessage(CLASSIFY_SYSTEM.format(business=get_settings().business_name)), HumanMessage(_email_text(message))], config
    )


@audited("classify")
async def classify(state: State, config: RunnableConfig) -> dict:
    s = get_settings()
    result = await classify_message(state["message"], config)
    out: dict[str, Any] = {"classification": result.model_dump()}
    if result.confidence < s.confidence_threshold:
        out.update(needs_human=True, human_reason=f"Low classification confidence ({result.confidence:.2f}): {result.reason}")
    store.update(state["message"]["id"], intent=result.intent, confidence=result.confidence)
    return out


def route_after_classify(state: State) -> str:
    if state.get("needs_human"):
        return "human_review"
    return "archive" if state["classification"]["intent"] == "spam_other" else "extract"


EXTRACT_SYSTEM = """Extract the requested fields from the message. Use null for anything that is not stated; never guess.
Today's date is {today}. Convert relative dates ("next Friday") to YYYY-MM-DD."""


async def extract_fields(intent: str, message: dict, config: RunnableConfig | None = None):
    llm = chat_model().with_structured_output(EXTRACTION_SCHEMAS[intent])
    return await llm.ainvoke(
        [SystemMessage(EXTRACT_SYSTEM.format(today=time.strftime("%Y-%m-%d (%A)"))), HumanMessage(_email_text(message))], config
    )


@audited("extract")
async def extract(state: State, config: RunnableConfig) -> dict:
    result = await extract_fields(state["classification"]["intent"], state["message"], config)
    return {"extraction": result.model_dump()}


@audited("act")
async def act(state: State, config: RunnableConfig) -> dict:
    """Deterministic tool calls against the ERP, chosen by intent."""
    intent = state["classification"]["intent"]
    x, m = state["extraction"], state["message"]
    facts: dict[str, Any] = {}
    attachments: list[str] = []
    if intent == "order_status":
        facts["order"] = erp.lookup_order(x.get("order_number"), m["sender"])
    elif intent == "quote_request":
        lines = erp.check_stock(x.get("items", []))
        facts["lines"] = lines
        facts["missing"] = [l["requested"] for l in lines if not l["matched"]] + [f"quantity for {l['requested']}" for l in lines if l["matched"] and not l.get("quantity")]
        quote = erp.generate_quote_pdf(m["sender"], x.get("company"), lines, x.get("delivery_date"))
        facts["quote"] = quote
        if quote.get("created"):
            attachments.append(quote["pdf"])
    elif intent == "complaint":
        order = erp.lookup_order(x.get("order_number"), m["sender"]) if x.get("order_number") else {"found": False}
        facts["order"] = order
        facts["ticket"] = erp.create_ticket(m["sender"], x.get("order_number"), x["issue_type"], x["severity"], x["summary"])
        facts["policy"] = ("Damaged, wrong or missing items: free replacement shipped within 2 business days, or a full refund for the affected "
                           "lines. Late deliveries over 3 days: 10% credit on the order. High-severity complaints get a call from the account "
                           "manager within 4 business hours.")
    elif intent == "invoice":
        facts["invoice"] = erp.lookup_invoice(x.get("invoice_number"), x.get("order_number"))
        facts["payment_details"] = "Bank transfer to Brightline Supplies, IBAN IE29 AIBK 9311 5212 3456 78, or card via the customer portal. Terms: Net 30."
    return {"facts": facts, "attachments": attachments}


DRAFT_SYSTEM = """You write email replies for {business}. Tone: {tone}
Rules:
- Use only the facts provided (from our systems). Never invent prices, dates, tracking numbers or policies.
- If something needed is missing (unknown product, missing quantity, order not found), ask for it politely.
- If a quote PDF was created, say it is attached and mention the quote number, total and validity.
- For complaints: acknowledge, apologise once, give the ticket number and the concrete remedy from the policy.
- Keep it under 170 words. No placeholders like [Name]."""


async def draft_reply(state: State, config: RunnableConfig | None = None) -> DraftReply:
    s = get_settings()
    llm = chat_model().with_structured_output(DraftReply)
    prompt = (f"Incoming message:\n{_email_text(state['message'])}\n\nIntent: {state['classification']['intent']}\n"
              f"Extracted fields: {state.get('extraction')}\nFacts from our systems: {state.get('facts')}\n"
              f"Attachments that will be sent: {state.get('attachments') or 'none'}")
    return await llm.ainvoke(
        [SystemMessage(DRAFT_SYSTEM.format(business=s.business_name, tone=s.business_tone)), HumanMessage(prompt)], config
    )


@audited("draft")
async def draft(state: State, config: RunnableConfig) -> dict:
    return {"draft": (await draft_reply(state, config)).model_dump()}


async def human_review(state: State) -> dict:
    """Pause until a reviewer acts. The payload is what the review UI shows.

    LangGraph re-runs this node from the top when it resumes, so nothing before `interrupt` may have side effects.
    """
    mid = state["message"]["id"]
    decision = interrupt({
        "draft": state.get("draft"),
        "needs_human": state.get("needs_human", False),
        "reason": state.get("human_reason"),
    })
    store.add_event(mid, "human_review", decision["action"], None, {"reviewer": decision.get("reviewer"), "note": decision.get("note")})
    return {"review": decision}


def route_after_review(state: State) -> str:
    return "send" if state["review"]["action"] in ("approve", "edit") else "reject"


@audited("send")
async def send(state: State, config: RunnableConfig) -> dict:
    review, m = state["review"], state["message"]
    original = state.get("draft") or {}
    subject = review.get("subject") or original.get("subject") or f"Re: {m.get('subject') or ''}".strip()
    body = review.get("body") or original.get("body")
    if not body:
        raise ValueError("nothing to send: no draft and no reviewer text")
    edited = review["action"] == "edit" and (body != original.get("body") or subject != original.get("subject"))
    delivery = await send_reply(m, subject, body, state.get("attachments", []))
    store.add_outbox(message_id=m["id"], channel=m["source"], recipient=m["sender"], subject=subject, body=body,
                     attachments=state.get("attachments", []), edited=edited, reviewer=review.get("reviewer"), delivery=delivery)
    return {"outcome": "sent", "delivery": delivery}


async def reject(state: State) -> dict:
    return {"outcome": "rejected"}


@audited("archive")
async def archive(state: State, config: RunnableConfig) -> dict:
    return {"outcome": "archived"}


# ------------------------------------------------------------------------------ graph
def build_graph() -> StateGraph:
    g = StateGraph(State)
    g.add_node("classify", classify, retry_policy=RETRY)
    g.add_node("extract", extract, retry_policy=RETRY)
    g.add_node("act", act, retry_policy=RETRY)
    g.add_node("draft", draft, retry_policy=RETRY)
    g.add_node("human_review", human_review)
    g.add_node("send", send, retry_policy=RETRY)
    g.add_node("reject", reject)
    g.add_node("archive", archive)

    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_after_classify, ["extract", "human_review", "archive"])
    g.add_edge("extract", "act")
    g.add_edge("act", "draft")
    g.add_edge("draft", "human_review")
    g.add_conditional_edges("human_review", route_after_review, ["send", "reject"])
    g.add_edge("send", END)
    g.add_edge("reject", END)
    g.add_edge("archive", END)
    return g


def mermaid() -> str:
    return build_graph().compile().get_graph().draw_mermaid()
