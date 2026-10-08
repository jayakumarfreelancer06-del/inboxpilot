import pytest

from inboxpilot import erp
from inboxpilot.config import get_settings


@pytest.fixture(autouse=True)
def erp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    erp.init_erp()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("text, sku", [
    ("medium corrugated boxes", "BX-M"), ("small boxes", "BX-S"), ("large corrugated boxes (60x40x40)", "BX-L"),
    ("clear packing tape", "TP-CL"), ("brown tape", "TP-BR"), ("bubble wrap", "BW-50"), ("EUR pallets", "PL-EU"),
    ("thermal shipping labels", "LB-TH"), ("mailer bags 25x35", "MB-25"), ("void fill paper", "VF-10"),
    ("cardboard edge protectors", "ED-CP"), ("stretch film", "SF-23"),
])
def test_product_matching(text, sku):
    assert erp.match_product(text)["sku"] == sku


def test_unknown_product_is_not_matched():
    assert erp.match_product("unicorn stickers") is None


def test_volume_discount_and_lead_time():
    [line] = erp.check_stock([{"product": "EUR pallets", "quantity": 60}])
    assert line["available_now"] is False and line["ship_in_days"] == 10
    [big] = erp.check_stock([{"product": "small boxes", "quantity": 5000}])
    assert big["discount_pct"] == 12 and big["line_total"] == round(5000 * 0.62 * 0.88, 2)


def test_lookup_order_by_number_or_sender():
    assert erp.lookup_order("order 10482")["number"] == "SO-10482"
    latest = erp.lookup_order(None, "grace@pixelparts.io")
    assert latest["number"] == "SO-10488" and latest["matched_by"] == "customer_email"
    assert erp.lookup_order("SO-99999")["found"] is False


def test_quote_pdf_is_created():
    lines = erp.check_stock([{"product": "medium boxes", "quantity": 2000}])
    q = erp.generate_quote_pdf("maya@example.com", "NorthPeak", lines, "2026-10-24")
    assert q["created"] and (get_settings().quotes_dir / q["pdf"]).read_bytes().startswith(b"%PDF")
