"""Classification accuracy and field-level extraction accuracy on the 30-email labelled set.

    python -m evals.run

Extraction is scored on the gold intent (so a classification miss doesn't hide extraction quality).
Quote items are matched to catalogue SKUs with the same matcher the agent uses, then compared with quantities.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path

from inboxpilot import erp
from inboxpilot.config import get_settings
from inboxpilot.graph import classify_message, extract_fields

HERE = Path(__file__).resolve().parent
INTENTS = ["quote_request", "order_status", "complaint", "invoice", "spam_other"]


def to_message(row: dict) -> dict:
    name, addr = parseaddr(row["from"])
    return {"id": row["id"], "source": "email", "sender": addr, "sender_name": name, "subject": row["subject"], "body": row["body"]}


def norm(v):
    if isinstance(v, str):
        return " ".join(v.lower().replace("é", "e").split())
    return v


def field_match(field: str, expected, actual) -> bool:
    if field == "items":
        got = Counter()
        for item in actual or []:
            p = erp.match_product(item.get("product") or "")
            got[(p["sku"] if p else None, item.get("quantity"))] += 1
        return got == Counter((i["sku"], i["quantity"]) for i in expected)
    if field in ("contact_name", "company") and expected and actual:
        return norm(expected) in norm(actual) or norm(actual) in norm(expected)
    if isinstance(expected, float) or isinstance(actual, float):
        return expected is not None and actual is not None and abs(float(expected) - float(actual)) < 0.01
    return norm(expected) == norm(actual)


async def evaluate(row: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        m = to_message(row)
        c = await classify_message(m)
        fields = {}
        if row["intent"] in ("quote_request", "order_status", "complaint", "invoice"):
            x = (await extract_fields(row["intent"], m)).model_dump()
            fields = {f: {"expected": exp, "actual": x.get(f), "ok": field_match(f, exp, x.get(f))} for f, exp in row["fields"].items()}
        return {"id": row["id"], "gold": row["intent"], "pred": c.intent, "confidence": c.confidence, "fields": fields}


async def main() -> None:
    erp.init_erp()
    rows = [json.loads(l) for l in (HERE / "dataset.jsonl").read_text().splitlines() if l.strip()]
    sem = asyncio.Semaphore(4)
    results = await asyncio.gather(*(evaluate(r, sem) for r in rows))

    s = get_settings()
    correct = sum(r["gold"] == r["pred"] for r in results)
    low_conf = [r for r in results if r["confidence"] < s.confidence_threshold]
    per_intent = defaultdict(lambda: [0, 0])
    for r in results:
        per_intent[r["gold"]][0] += r["gold"] == r["pred"]
        per_intent[r["gold"]][1] += 1
    per_field = defaultdict(lambda: [0, 0])
    for r in results:
        for f, v in r["fields"].items():
            per_field[f][0] += v["ok"]
            per_field[f][1] += 1
    field_ok = sum(v[0] for v in per_field.values())
    field_n = sum(v[1] for v in per_field.values())
    confusion = Counter((r["gold"], r["pred"]) for r in results)

    lines = [
        "# InboxPilot eval report", "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · {len(rows)} labelled emails · model `{s.llm_model}`", "",
        "| Metric | Result |", "|---|---|",
        f"| Classification accuracy | **{correct}/{len(rows)} ({correct / len(rows):.0%})** |",
        f"| Extraction field-level accuracy | **{field_ok}/{field_n} ({field_ok / field_n:.0%})** |",
        f"| Routed to a human (confidence < {s.confidence_threshold}) | {len(low_conf)}/{len(rows)} |",
        f"| Misclassified but confident (would reach a draft) | {sum(1 for r in results if r['gold'] != r['pred'] and r['confidence'] >= s.confidence_threshold)} |",
        "", "## Classification by intent", "", "| Intent | Correct |", "|---|---|",
        *[f"| {i} | {per_intent[i][0]}/{per_intent[i][1]} |" for i in INTENTS if per_intent[i][1]],
        "", "## Confusion matrix (rows = gold, columns = predicted)", "",
        "| gold \\ pred | " + " | ".join(INTENTS) + " |", "|---|" + "---|" * len(INTENTS),
        *[f"| {g} | " + " | ".join(str(confusion[(g, p)] or "·") for p in INTENTS) + " |" for g in INTENTS],
        "", "## Extraction by field", "", "| Field | Correct |", "|---|---|",
        *[f"| {f} | {v[0]}/{v[1]} |" for f, v in sorted(per_field.items())],
    ]
    misses = [(r["id"], f, v) for r in results for f, v in r["fields"].items() if not v["ok"]]
    if misses:
        lines += ["", "## Extraction misses", "", "| Email | Field | Expected | Got |", "|---|---|---|---|"]
        lines += [f"| {i} | {f} | {json.dumps(v['expected'])} | {json.dumps(v['actual'])[:120]} |" for i, f, v in misses]
    report = "\n".join(lines) + "\n"
    (HERE / "report.md").write_text(report)
    (HERE / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
