"""Structured outputs: classification and the intent-specific extraction schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["quote_request", "order_status", "complaint", "invoice", "spam_other"]


class Classification(BaseModel):
    intent: Intent = Field(description="quote_request: asks for prices/availability for products. order_status: asks where an existing order is or when it arrives. "
                                       "complaint: unhappy about a product, delivery or service. invoice: about an invoice, payment or billing document. "
                                       "spam_other: marketing, newsletters, phishing, or anything that needs no business reply.")
    confidence: float = Field(ge=0, le=1, description="How sure you are, 0 to 1. Use below 0.7 when the email mixes intents or is ambiguous.")
    reason: str = Field(description="One short sentence explaining the decision.")


class QuoteItem(BaseModel):
    product: str = Field(description="Product as the customer wrote it.")
    quantity: int | None = Field(default=None, description="Units requested, as an integer.")


class QuoteRequest(BaseModel):
    items: list[QuoteItem] = Field(description="Every product the customer asks about.")
    delivery_date: str | None = Field(default=None, description="Requested delivery date as YYYY-MM-DD if a specific date is given, else null.")
    delivery_location: str | None = Field(default=None, description="City or address for delivery, if given.")
    contact_name: str | None = None
    company: str | None = None
    phone: str | None = None


class OrderStatusRequest(BaseModel):
    order_number: str | None = Field(default=None, description="Order number exactly as written, normalised to the form SO-12345 when it is clearly one.")
    contact_name: str | None = None
    company: str | None = None


class Complaint(BaseModel):
    order_number: str | None = Field(default=None, description="Order number normalised to SO-12345 if mentioned.")
    issue_type: Literal["damaged", "late", "wrong_item", "missing_items", "quality", "billing", "service", "other"]
    severity: Literal["low", "medium", "high"] = Field(description="high: angry, threatens to leave/escalate, or business impact. low: mild.")
    requested_resolution: Literal["refund", "replacement", "credit", "apology", "callback", "none"] = "none"
    summary: str = Field(description="One-sentence summary of the problem.")
    contact_name: str | None = None


class InvoiceRequest(BaseModel):
    invoice_number: str | None = Field(default=None, description="Invoice number normalised to INV-12345 if mentioned.")
    order_number: str | None = Field(default=None, description="Order number normalised to SO-12345 if mentioned.")
    request_type: Literal["copy", "correction", "payment_confirmation", "dispute", "other"]
    amount: float | None = Field(default=None, description="Amount mentioned, as a number without currency symbol.")
    contact_name: str | None = None


EXTRACTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "quote_request": QuoteRequest,
    "order_status": OrderStatusRequest,
    "complaint": Complaint,
    "invoice": InvoiceRequest,
}


class DraftReply(BaseModel):
    subject: str
    body: str = Field(description="The reply body in plain text, ready to send.")
    internal_note: str = Field(description="One line for the reviewer: what to double-check before sending.")
