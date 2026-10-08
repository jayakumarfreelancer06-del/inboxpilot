"""A tiny SQLite "ERP" for Brightline Supplies (fictional B2B packaging supplier) and the tools the agent can call."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from inboxpilot.config import get_settings

PRODUCTS = [
    # sku, name, unit, unit price (USD), stock, lead time days when out of stock
    ("BX-S", "Corrugated box small (30x20x15 cm)", "box", 0.62, 12000, 5),
    ("BX-M", "Corrugated box medium (40x30x25 cm)", "box", 0.95, 8000, 5),
    ("BX-L", "Corrugated box large (60x40x40 cm)", "box", 1.60, 900, 7),
    ("TP-CL", "Packing tape clear 48mm x 66m", "roll", 1.10, 5400, 4),
    ("TP-BR", "Packing tape brown 48mm x 66m", "roll", 1.10, 300, 4),
    ("BW-50", "Bubble wrap roll 50cm x 100m", "roll", 18.50, 220, 6),
    ("SF-23", "Stretch film 23 micron 500mm x 300m", "roll", 9.80, 640, 6),
    ("PL-EU", "Wooden EUR pallet 1200x800", "pallet", 14.00, 0, 10),
    ("LB-TH", "Thermal shipping labels 100x150 (500/roll)", "roll", 7.40, 1500, 3),
    ("MB-25", "Mailer bags 25x35 cm (100 pack)", "pack", 6.90, 410, 5),
    ("VF-10", "Void fill paper 10 kg", "bundle", 21.00, 75, 8),
    ("ED-CP", "Edge protectors cardboard 1m", "piece", 0.45, 9800, 4),
]

# volume discounts on the line total
DISCOUNTS = [(5000, 0.12), (1000, 0.08), (250, 0.04)]

CUSTOMERS = [
    ("Maya Chen", "maya.chen@northpeak-outdoor.com", "NorthPeak Outdoor"),
    ("Daniel Okafor", "d.okafor@greenleafgrocers.co", "GreenLeaf Grocers"),
    ("Sofia Rossi", "sofia@rossiceramics.it", "Rossi Ceramics"),
    ("Liam Murphy", "liam.murphy@harbourbooks.ie", "Harbour Books"),
    ("Priya Nair", "priya@lumencosmetics.com", "Lumen Cosmetics"),
    ("Jonas Weber", "jonas.weber@kraftwerk-tools.de", "Kraftwerk Tools"),
    ("Grace Kim", "grace@pixelparts.io", "PixelParts"),
    ("Omar Haddad", "omar@cedarandsage.com", "Cedar & Sage Home"),
]

ORDERS = [
    # number, customer email, status, days ago placed, carrier, tracking, eta offset days, total
    ("SO-10482", "maya.chen@northpeak-outdoor.com", "shipped", 3, "DHL", "JD014600006281", 2, 1840.00),
    ("SO-10477", "d.okafor@greenleafgrocers.co", "delivered", 9, "UPS", "1Z999AA10123456784", -2, 932.50),
    ("SO-10490", "sofia@rossiceramics.it", "processing", 1, None, None, 4, 2210.00),
    ("SO-10455", "liam.murphy@harbourbooks.ie", "delivered", 16, "DPD", "15501234567890", -8, 418.00),
    ("SO-10493", "priya@lumencosmetics.com", "backordered", 2, None, None, 9, 3120.00),
    ("SO-10461", "jonas.weber@kraftwerk-tools.de", "shipped", 6, "DHL", "JD014600006977", -1, 760.40),
    ("SO-10488", "grace@pixelparts.io", "delivered", 5, "UPS", "1Z999AA10123459931", -1, 289.90),
    ("SO-10470", "omar@cedarandsage.com", "delivered", 12, "DPD", "15501234569912", -5, 1275.00),
]

INVOICES = [
    # number, order, amount, issued days ago, due in days, status
    ("INV-20931", "SO-10477", 932.50, 9, 21, "unpaid"),
    ("INV-20918", "SO-10455", 418.00, 16, 14, "paid"),
    ("INV-20940", "SO-10482", 1840.00, 3, 27, "unpaid"),
    ("INV-20925", "SO-10461", 760.40, 6, 24, "unpaid"),
    ("INV-20902", "SO-10470", 1275.00, 12, -2, "overdue"),
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().erp_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_erp(reset: bool = False) -> None:
    path: Path = get_settings().erp_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if reset and path.exists():
        path.unlink()
    with closing(connect()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, name TEXT, unit TEXT, price REAL, stock INTEGER, lead_time_days INTEGER);
            CREATE TABLE IF NOT EXISTS customers (email TEXT PRIMARY KEY, name TEXT, company TEXT);
            CREATE TABLE IF NOT EXISTS orders (number TEXT PRIMARY KEY, customer_email TEXT, status TEXT, placed_on TEXT, carrier TEXT,
                                               tracking TEXT, eta TEXT, total REAL);
            CREATE TABLE IF NOT EXISTS invoices (number TEXT PRIMARY KEY, order_number TEXT, amount REAL, issued_on TEXT, due_on TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, customer_email TEXT, order_number TEXT,
                                                issue_type TEXT, severity TEXT, summary TEXT, status TEXT DEFAULT 'open');
            CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, created_at TEXT, customer_email TEXT,
                                               total REAL, valid_until TEXT, pdf_path TEXT);
            """
        )
        if conn.execute("SELECT count(*) FROM products").fetchone()[0]:
            return
        today = date.today()
        conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", PRODUCTS)
        conn.executemany("INSERT INTO customers VALUES (?,?,?)", [(e, n, c) for n, e, c in CUSTOMERS])
        conn.executemany(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
            [(n, e, s, (today - timedelta(days=d)).isoformat(), car, tr, (today + timedelta(days=eta)).isoformat(), t) for n, e, s, d, car, tr, eta, t in ORDERS],
        )
        conn.executemany(
            "INSERT INTO invoices VALUES (?,?,?,?,?,?)",
            [(n, o, a, (today - timedelta(days=i)).isoformat(), (today + timedelta(days=due)).isoformat(), s) for n, o, a, i, due, s in INVOICES],
        )


def _norm_order(number: str | None) -> str | None:
    if not number:
        return None
    digits = re.sub(r"\D", "", number)
    return f"SO-{digits}" if digits else None


# ------------------------------------------------------------------ tools
def lookup_order(order_number: str | None = None, customer_email: str | None = None) -> dict:
    """Find an order by number, or the customer's most recent order."""
    with closing(connect()) as conn:
        row = None
        number = _norm_order(order_number)
        if number:
            row = conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
        if row is None and customer_email:
            row = conn.execute("SELECT * FROM orders WHERE customer_email = ? ORDER BY placed_on DESC LIMIT 1", (customer_email.lower(),)).fetchone()
        if row is None:
            return {"found": False, "searched_for": number or customer_email}
        order = dict(row)
        order["found"] = True
        order["matched_by"] = "order_number" if number and order["number"] == number else "customer_email"
        order["customer_on_file"] = (customer_email or "").lower() == order["customer_email"]
        inv = conn.execute("SELECT number, amount, status, due_on FROM invoices WHERE order_number = ?", (order["number"],)).fetchone()
        order["invoice"] = dict(inv) if inv else None
        return order


def match_product(text: str) -> dict | None:
    """Fuzzy-match a free-text product name against the catalogue."""
    with closing(connect()) as conn:
        products = [dict(r) for r in conn.execute("SELECT * FROM products")]
    t = text.lower()

    def score(p: dict) -> float:
        name = p["name"].lower()
        s = SequenceMatcher(None, t, name).ratio()
        words = set(re.findall(r"[a-z]+", t)) - {"the", "a", "of", "and", "for", "x", "cm", "mm"}
        overlap = len(words & set(re.findall(r"[a-z]+", name))) / max(1, len(words))
        size = 0.3 if any(k in t for k in ("small", "medium", "large")) and any(k in t and k in name for k in ("small", "medium", "large")) else 0
        colour = 0.2 if any(k in t and k in name for k in ("clear", "brown")) else 0
        return s * 0.4 + overlap * 0.6 + size + colour

    best = max(products, key=score)
    return best if score(best) >= 0.45 else None


def check_stock(items: list[dict]) -> list[dict]:
    """Price and availability for each requested item, with volume discounts."""
    out = []
    for item in items:
        p = match_product(item.get("product") or "")
        qty = item.get("quantity")
        if p is None:
            out.append({"requested": item.get("product"), "quantity": qty, "matched": False})
            continue
        line = {"requested": item.get("product"), "matched": True, "sku": p["sku"], "name": p["name"], "unit": p["unit"],
                "quantity": qty, "unit_price": p["price"], "in_stock": p["stock"]}
        if qty:
            discount = next((d for threshold, d in DISCOUNTS if qty >= threshold), 0.0)
            gross = qty * p["price"]
            line.update(discount_pct=int(discount * 100), line_total=round(gross * (1 - discount), 2),
                        available_now=qty <= p["stock"],
                        ship_in_days=2 if qty <= p["stock"] else p["lead_time_days"])
        out.append(line)
    return out


def generate_quote_pdf(customer_email: str, company: str | None, lines: list[dict], delivery_date: str | None) -> dict:
    """Create a quote record and a PDF. Only priced lines are included."""
    s = get_settings()
    priced = [l for l in lines if l.get("matched") and l.get("quantity")]
    if not priced:
        return {"created": False, "reason": "no priced lines (missing quantities or unknown products)"}
    s.quotes_dir.mkdir(parents=True, exist_ok=True)
    total = round(sum(l["line_total"] for l in priced), 2)
    valid_until = (date.today() + timedelta(days=30)).isoformat()
    with closing(connect()) as conn, conn:
        cur = conn.execute("INSERT INTO quotes (created_at, customer_email, total, valid_until) VALUES (?,?,?,?)",
                           (datetime.now().isoformat(timespec="seconds"), customer_email, total, valid_until))
        number = f"Q-{3000 + cur.lastrowid}"
        path = s.quotes_dir / f"{number}.pdf"
        conn.execute("UPDATE quotes SET number = ?, pdf_path = ? WHERE id = ?", (number, str(path), cur.lastrowid))

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=f"Quote {number}")
    rows = [["SKU", "Item", "Qty", "Unit price", "Discount", "Line total"]] + [
        [l["sku"], Paragraph(l["name"], styles["BodyText"]), f"{l['quantity']:,}", f"${l['unit_price']:.2f}", f"{l['discount_pct']}%", f"${l['line_total']:,.2f}"]
        for l in priced
    ] + [["", "", "", "", "Total", f"${total:,.2f}"]]
    table = Table(rows, colWidths=[50, 200, 50, 65, 55, 75])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("GRID", (0, 0), (-1, -2), 0.4, colors.grey), ("FONTNAME", (-2, -1), (-1, -1), "Helvetica-Bold"),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story = [Paragraph(f"{s.business_name} — Quote {number}", styles["Title"]),
             Paragraph(f"Prepared for: {company or customer_email}<br/>Date: {date.today().isoformat()} · Valid until: {valid_until}"
                       + (f"<br/>Requested delivery: {delivery_date}" if delivery_date else ""), styles["BodyText"]),
             Spacer(1, 12), table, Spacer(1, 12),
             Paragraph("Prices in USD, excluding VAT and delivery. Volume discounts: 4% from 250 units, 8% from 1,000, 12% from 5,000.", styles["Italic"])]
    doc.build(story)
    return {"created": True, "quote_number": number, "total": total, "valid_until": valid_until, "pdf": path.name}


def create_ticket(customer_email: str, order_number: str | None, issue_type: str, severity: str, summary: str) -> dict:
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO tickets (created_at, customer_email, order_number, issue_type, severity, summary) VALUES (?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), customer_email, _norm_order(order_number), issue_type, severity, summary),
        )
        return {"ticket_id": f"T-{cur.lastrowid:05d}", "severity": severity, "status": "open"}


def lookup_invoice(invoice_number: str | None = None, order_number: str | None = None) -> dict:
    with closing(connect()) as conn:
        row = None
        if invoice_number:
            digits = re.sub(r"\D", "", invoice_number)
            row = conn.execute("SELECT * FROM invoices WHERE number = ?", (f"INV-{digits}",)).fetchone()
        if row is None and order_number:
            row = conn.execute("SELECT * FROM invoices WHERE order_number = ?", (_norm_order(order_number),)).fetchone()
        return {"found": True, **dict(row)} if row else {"found": False, "searched_for": invoice_number or order_number}
