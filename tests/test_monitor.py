"""Pruebas sin red: simulan el sitio con HTML copiado de la estructura real (18-nov-2026)."""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor  # noqa: E402


def card(sku, name, price, disabled=False):
    cls = "btn btn-default btn-add-to-cart" + (" disabled" if disabled else "")
    return f"""
<div class="panel panel-product">
  <div class="panel-heading"><a class="heart-icon-tooltip hidden" data-sku="{sku}"></a></div>
  <div class="panel-body product-body">
    <a class="info" title="{name}" href="/shop/products/{sku.lower()}?taxon_id=30">{name}</a>
    <div>Retail :
          ${price:.2f}
    | VIP : $1.00</div>
    <button name="button" type="submit" class="{cls}" id="add-to-cart-button_{sku}" title="Add">ADD</button>
  </div>
</div>"""


def page(cards):
    return "<html><body>" + "".join(cards) + "</body></html>"


def listing(flag):
    return f"""<html><head><meta name="csrf-token" content="tok123"></head><body>
<img alt="{flag}" src="https://cdn/assets/templates/default/corporate/{flag[-2:]}_active-abc.png">
</body></html>"""


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class FakeSession:
    """Imita el sitio: el mercado depende de la cookie site_market_id fijada por el POST."""

    def __init__(self, catalog, flag_for, set_market=True):
        self.headers = {}
        self.cookies = []
        self.catalog, self.flag_for, self.set_market = catalog, flag_for, set_market
        self.market = 2  # US por defecto

    def get(self, url, **kw):
        if url.endswith(monitor.LISTING_PATH):
            return FakeResp(listing(self.flag_for[self.market]))
        page_no = int(url.split("page=")[1].split("&")[0])
        cards = self.catalog.get(self.market, [])
        chunk = cards[(page_no - 1) * 16: page_no * 16]
        return FakeResp(page(chunk) if chunk else page(cards[:16]))  # al final repite la pág. 1, como el sitio

    def post(self, url, params=None, headers=None, **kw):
        assert headers["X-CSRF-Token"] == "tok123"
        if self.set_market:
            self.market = params["market_id"]
            self.cookies = [FakeCookie("site_market_id", str(params["market_id"]))]
        return FakeResp("window.location = 'http://shop.mymonat.com'")


def catalog_au(oos_toner=True, cleanser_back=False):
    cards = [card(monitor.REFERENCE_SKU, "MONAT GLOSS MODE™ Shine Infusion Conditioner", 91)]
    cards += [card(f"60000090{i:02d}", f"Producto {i}", 50 + i) for i in range(60)]
    if oos_toner:
        cards.append(card("6000006005so", "Barrier Booster™ Ceramide Infusion Toner - Out of Stock", 80, disabled=True))
    else:
        cards.append(card("6000006005", "Barrier Booster™ Ceramide Infusion Toner", 80))
    if cleanser_back:
        cards.append(card("6000006004", "Barrier Booster™ Hydra Refresh Cleanser", 70))
    else:
        cards.append(card("6000006004SO", "Barrier Booster™ Hydra Refresh Cleanser - Out of Stock", 70, disabled=True))
    return cards


FLAGS = {2: "Monat US", 18: "Monat AU", 20: "Monat NZ"}


@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(monitor, "HISTORY_DIR", tmp_path / "historial")
    return tmp_path


def run(monkeypatch, catalog, set_market=True, code="AU"):
    monkeypatch.setattr(monitor.requests, "Session", lambda: FakeSession(catalog, FLAGS, set_market))
    return monitor.run_market(code, monitor.MARKETS[code], "2026-11-23T12:00:00Z")


def test_parse_real_structure():
    items = monitor.parse_products(page([card("6000006005so", "Toner - Out of Stock", 80, True), card("6000007063", "Cond", 91)]))
    assert [(i.base_sku, i.out_of_stock, i.price) for i in items] == [("6000006005", True, 80.0), ("6000007063", False, 91.0)]
    assert monitor.clean_name(items[0].name) == "Toner"


def test_baseline_then_diff(tmp_dirs, monkeypatch):
    r1 = run(monkeypatch, {18: catalog_au()})
    assert r1.verified and r1.baseline
    assert sum(v["out"] for v in r1.products.values()) == 2

    # Semana 2: el toner volvió (SKU sin SO), el cleanser sigue agotado, se agota "Producto 3"
    cards = catalog_au(oos_toner=False)
    cards[4] = card("6000009003", "Producto 3", 53, disabled=True)
    r2 = run(monkeypatch, {18: cards})
    assert r2.verified and not r2.baseline
    assert [x[0] for x in r2.back_in] == ["6000006005"]
    assert [x[0] for x in r2.went_out] == ["6000009003"]
    assert r2.new_items == [] and r2.removed == []

    report = monitor.build_report([r2], "2026-11-23")
    assert "Se agotaron (1)" in report and "Producto 3" in report
    assert "Volvieron a tener stock (1)" in report and "Ceramide Infusion Toner (SKU 6000006005)" in report
    # Link nuevo (sin SO) con protocolo de mercado, y el link anterior (con SO) para reemplazarlo
    assert "  Link: https://corp.mymonat.com/shop/products/6000006005?globallanguage=en&globalmarket=au" in report
    assert "  Link anterior: https://corp.mymonat.com/shop/products/6000006005so?globallanguage=en&globalmarket=au" in report
    # El producto que se agotó también trae su link
    assert "  Link: https://corp.mymonat.com/shop/products/6000009003?globallanguage=en&globalmarket=au" in report


def test_market_link_format():
    url = "https://shop.mymonat.com/shop/products/renew-shampoo-6000005514?taxon_id=30"
    assert monitor.market_link(url, "AU") == "https://corp.mymonat.com/shop/products/renew-shampoo-6000005514?globallanguage=en&globalmarket=au"
    assert monitor.market_link(url, "NZ") == "https://corp.mymonat.com/shop/products/renew-shampoo-6000005514?globallanguage=en&globalmarket=nz"


def test_wrong_market_is_not_reported(tmp_dirs, monkeypatch):
    # El POST no cambia el mercado: el sitio se queda en US
    us = [card(monitor.REFERENCE_SKU, "Cond", 57)] + [card(f"60000090{i:02d}", f"P{i}", 10) for i in range(60)]
    r = run(monkeypatch, {2: us}, set_market=False)
    assert not r.verified
    assert not (tmp_dirs / "state" / "AU.json").exists()  # no se toca el historial
    report = monitor.build_report([r], "2026-11-23")
    assert "No se pudo confirmar" in report and "❌ bandera del encabezado" in report


def test_nz_price_check(tmp_dirs, monkeypatch):
    # NZ con precio de AU (91) debe fallar la verificación de precio
    r = run(monkeypatch, {20: catalog_au()}, code="NZ")
    assert not r.verified
    assert r.checks["precio de referencia"][0] is False
    assert r.checks["bandera del encabezado"][0] is True


def test_weekly_history_and_spreadsheet(tmp_dirs, monkeypatch):
    from openpyxl import load_workbook

    run(monkeypatch, {18: catalog_au()})
    cards = catalog_au(oos_toner=False)
    cards[4] = card("6000009003", "Producto 3", 53, disabled=True)
    r2 = run(monkeypatch, {18: cards})
    report = monitor.build_report([r2], "2026-11-23")
    folder = monitor.save_history([r2], report, "2026-11-25")  # miércoles -> carpeta del lunes de esa semana

    assert folder.name == "2026-11-23_W48"
    assert sorted(f.name for f in folder.iterdir()) == ["estado_AU.json", "reporte.md", "reporte_stock_2026-11-23_W48.xlsx"]

    wb = load_workbook(folder / "reporte_stock_2026-11-23_W48.xlsx")
    assert wb.sheetnames == ["Resumen", "Cambios", "Agotados AU"]
    rows = [[c.value for c in r] for r in wb["Cambios"].iter_rows(min_row=2)]
    assert ["AU", "Se agotó", "6000009003"] == rows[0][:3] or ["AU", "Se agotó", "6000009003"] == rows[1][:3]
    back = next(r for r in rows if r[1] == "Volvió a tener stock")
    assert back[4].endswith("/6000006005?globallanguage=en&globalmarket=au")
    assert back[5].endswith("/6000006005so?globallanguage=en&globalmarket=au")
    inv = wb["Agotados AU"]
    n_out = sum(v["out"] for v in r2.products.values())
    assert inv.max_row == 1 + n_out  # solo agotados, sin disponibles
    assert all(c.value == "Agotado" for c in inv["C"][1:])
    assert wb["Resumen"]["D5"].value == len(r2.products)
    assert wb["Resumen"]["E5"].value == '=COUNTIF(\'Agotados AU\'!$C:$C,"Agotado")'


def test_first_scan_is_kept_separately(tmp_dirs, monkeypatch):
    r1 = run(monkeypatch, {18: catalog_au()})
    folder = monitor.save_history([r1], "primer reporte", "2026-11-23")
    first = folder / "primer-barrido"
    assert (first / "reporte.md").read_text() == "primer reporte"
    assert (first / "reporte_stock_primer-barrido_2026-11-23.xlsx").exists()
    assert (first / "estado_AU.json").exists()

    # Otra ejecución la misma semana sobrescribe la carpeta semanal, pero no el primer barrido
    r2 = run(monkeypatch, {18: catalog_au(oos_toner=False)})
    monitor.save_history([r2], "segundo reporte", "2026-11-24")
    assert (folder / "reporte.md").read_text() == "segundo reporte"
    assert (first / "reporte.md").read_text() == "primer reporte"
