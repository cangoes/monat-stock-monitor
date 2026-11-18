#!/usr/bin/env python3
"""
Monitor semanal de productos agotados (sold out) en MONAT Australia y Nueva Zelanda.

Flujo por mercado:
  1. Abre una sesión nueva y selecciona el mercado (POST a change_site_market).
  2. Verifica que el sitio realmente esté en ese mercado (cookie, bandera y precio de referencia).
  3. Recorre todas las páginas del listado "All products" y lee el estado de cada producto.
  4. Compara con el barrido anterior (state/<MERCADO>.json) por SKU base (sin sufijo "SO").
  5. Genera un reporte con los productos que se agotaron y los que volvieron a tener stock,
     lo guarda en historial/<fecha>_W<semana>/ (reporte en Markdown + copia del estado)
     y lo envía por los canales configurados.

Si la verificación de un mercado falla, ese mercado NO se compara ni se actualiza su estado,
el reporte lo indica y el script termina con código 1 (GitHub marca la ejecución como fallida).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import smtplib
import sys
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------------------

BASE_URL = os.environ.get("MONAT_BASE_URL", "https://shop.mymonat.com")
LISTING_PATH = "/shop/t/categories/all-products"
PAGE_PATH = "/shop/products?sort_scope=&page={page}&taxon={taxon}&infiniteScroll=true"
# Los links del reporte usan el sitio corp con el protocolo de mercado en la URL, p. ej.:
#   https://corp.mymonat.com/shop/products/renew-shampoo-6000005514?globallanguage=en&globalmarket=au
LINK_HOST = os.environ.get("MONAT_LINK_HOST", "https://corp.mymonat.com")
TAXON_ID = os.environ.get("MONAT_TAXON_ID", "30")  # taxon de "All products"
MAX_PAGES = 40
MIN_PRODUCTS = int(os.environ.get("MIN_PRODUCTS", "50"))  # menos que esto = página rota

# Producto de referencia para comprobar precios (GLOSS MODE Shine Infusion Conditioner).
# Precios Retail observados el 18-nov-2026: US $57, AU $91, NZ $113.
REFERENCE_SKU = "6000007063"

MARKETS = {
    "AU": {
        "name": "Australia",
        "market_id": 18,
        "language_id": 1,
        "flag_alt": "Monat AU",
        "link_language": "en",
        "ref_price_range": (75, 110),  # rango amplio para tolerar cambios de precio
    },
    "NZ": {
        "name": "Nueva Zelanda",
        "market_id": 20,
        "language_id": 1,
        "flag_alt": "Monat NZ",
        "link_language": "en",
        "ref_price_range": (95, 135),
    },
}

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
HISTORY_DIR = ROOT / "historial"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}

OOS_NAME_RE = re.compile(r"\s*[-–]\s*(out of stock|sold out)\s*$", re.I)
SO_SUFFIX_RE = re.compile(r"so$", re.I)
PRICE_RE = re.compile(r"Retail\s*:\s*\$\s*([\d.,]+)", re.I)


# --------------------------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------------------------

@dataclass
class Product:
    sku: str          # SKU tal como aparece (puede terminar en SO)
    base_sku: str     # SKU sin sufijo SO; es la llave de comparación
    name: str
    out_of_stock: bool
    price: float | None
    url: str


@dataclass
class MarketResult:
    code: str
    verified: bool = False
    checks: dict = field(default_factory=dict)
    error: str | None = None
    products: dict = field(default_factory=dict)  # base_sku -> {"name", "out", "skus"}
    went_out: list = field(default_factory=list)
    back_in: list = field(default_factory=list)
    new_items: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    baseline: bool = False


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------

def base_sku(sku: str) -> str:
    return SO_SUFFIX_RE.sub("", sku.strip())


def market_link(url: str, code: str) -> str:
    """Convierte la URL del listado al formato con protocolo de mercado (globallanguage/globalmarket)."""
    path = urlparse(url).path
    lang = MARKETS[code].get("link_language", "en")
    return f"{LINK_HOST}{path}?globallanguage={lang}&globalmarket={code.lower()}"


def clean_name(name: str) -> str:
    return OOS_NAME_RE.sub("", " ".join(name.split()))


def parse_price(text: str) -> float | None:
    m = PRICE_RE.search(" ".join(text.split()))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_products(html: str) -> list[Product]:
    """Lee las tarjetas .panel-product de una página del listado."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[Product] = []
    for card in soup.select(".panel-product"):
        btn = card.select_one(".btn-add-to-cart")
        sku = ""
        if btn is not None and btn.get("id", "").startswith("add-to-cart-button_"):
            sku = btn["id"].split("add-to-cart-button_", 1)[1]
        if not sku:
            tagged = card.select_one("[data-sku]")
            sku = tagged.get("data-sku", "") if tagged else ""
        if not sku:
            continue
        link = card.select_one("a.info")
        name = link.get_text(" ", strip=True) if link else sku
        classes = btn.get("class", []) if btn is not None else []
        out = btn is None or "disabled" in classes or btn.has_attr("disabled")
        items.append(
            Product(
                sku=sku,
                base_sku=base_sku(sku),
                name=name,
                out_of_stock=out,
                price=parse_price(card.get_text(" ")),
                url=BASE_URL + (link.get("href", "") if link else ""),
            )
        )
    return items


def active_flag_alt(html: str) -> str | None:
    """Texto alternativo de la bandera activa del encabezado (ej. 'Monat AU')."""
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        if "_active" in (img.get("src") or ""):
            return (img.get("alt") or "").strip()
    return None


def csrf_token(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "csrf-token"})
    return meta.get("content") if meta else None


# --------------------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------------------

def select_market(session: requests.Session, cfg: dict) -> str:
    """Abre la página, selecciona el mercado y devuelve el HTML del listado ya en ese mercado."""
    first = session.get(BASE_URL + LISTING_PATH, timeout=60)
    first.raise_for_status()
    token = csrf_token(first.text)
    if not token:
        raise RuntimeError("No se encontró el token CSRF en la página inicial")
    resp = session.post(
        f"{BASE_URL}/render_site/shop/change_site_market",
        params={"language_id": cfg["language_id"], "market_id": cfg["market_id"]},
        headers={
            "X-CSRF-Token": token,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/javascript, application/javascript",
        },
        timeout=60,
        allow_redirects=False,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"El cambio de mercado respondió {resp.status_code}")
    listing = session.get(BASE_URL + LISTING_PATH, timeout=60)
    listing.raise_for_status()
    return listing.text


def fetch_all_products(session: requests.Session) -> list[Product]:
    seen: dict[str, Product] = {}
    for page in range(1, MAX_PAGES + 1):
        r = session.get(
            BASE_URL + PAGE_PATH.format(page=page, taxon=TAXON_ID),
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=60,
        )
        r.raise_for_status()
        new = 0
        for p in parse_products(r.text):
            if p.sku not in seen:
                seen[p.sku] = p
                new += 1
        if new == 0:  # página vacía o repetida = fin del listado
            break
    return list(seen.values())


def group_by_base(products: list[Product]) -> dict:
    """Agrupa variantes (normal y SO) por SKU base. Disponible si alguna variante lo está."""
    grouped: dict[str, dict] = {}
    for p in products:
        g = grouped.setdefault(p.base_sku, {"name": clean_name(p.name), "skus": [], "available": False, "url": p.url})
        g["skus"].append(p.sku)
        if not p.out_of_stock:
            g["available"] = True
            g["name"] = clean_name(p.name)
            g["url"] = p.url
    return {
        k: {"name": v["name"], "out": not v["available"], "skus": sorted(v["skus"]), "url": v["url"]}
        for k, v in grouped.items()
    }


def verify_market(code: str, cfg: dict, session: requests.Session, listing_html: str,
                  products: list[Product]) -> tuple[bool, dict]:
    checks = {}
    values = [c.value for c in session.cookies if c.name == "site_market_id"]
    cookie = values[-1] if values else None
    checks["cookie site_market_id"] = (str(cookie) == str(cfg["market_id"]), f"{cookie} (esperado {cfg['market_id']})")
    flag = active_flag_alt(listing_html)
    checks["bandera del encabezado"] = (flag == cfg["flag_alt"], f"{flag!r} (esperado {cfg['flag_alt']!r})")
    ref = next((p for p in products if p.base_sku == REFERENCE_SKU and p.price), None)
    lo, hi = cfg["ref_price_range"]
    if ref is None:
        checks["precio de referencia"] = (False, f"SKU {REFERENCE_SKU} no encontrado")
    else:
        checks["precio de referencia"] = (lo <= ref.price <= hi, f"${ref.price:.2f} (esperado entre ${lo} y ${hi})")
    checks["cantidad de productos"] = (len(products) >= MIN_PRODUCTS, f"{len(products)} (mínimo {MIN_PRODUCTS})")
    return all(ok for ok, _ in checks.values()), checks


# --------------------------------------------------------------------------------------
# Comparación y estado
# --------------------------------------------------------------------------------------

def load_state(code: str) -> dict | None:
    path = STATE_DIR / f"{code}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(code: str, products: dict, when: str) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / f"{code}.json").write_text(
        json.dumps({"scanned_at": when, "products": products}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def diff(prev: dict, curr: dict) -> tuple[list, list, list, list]:
    went_out, back_in, new_items, removed = [], [], [], []
    for sku, item in curr.items():
        old = prev.get(sku)
        if old is None:
            new_items.append((sku, item, None))
        elif not old["out"] and item["out"]:
            went_out.append((sku, item, old))
        elif old["out"] and not item["out"]:
            back_in.append((sku, item, old))
    for sku, item in prev.items():
        if sku not in curr:
            removed.append((sku, item, None))
    key = lambda x: x[1]["name"].lower()
    return sorted(went_out, key=key), sorted(back_in, key=key), sorted(new_items, key=key), sorted(removed, key=key)


def run_market(code: str, cfg: dict, when: str) -> MarketResult:
    res = MarketResult(code=code)
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        listing_html = select_market(session, cfg)
        products = fetch_all_products(session)
        res.verified, res.checks = verify_market(code, cfg, session, listing_html, products)
        if not res.verified:
            return res
        res.products = group_by_base(products)
        prev = load_state(code)
        if prev is None:
            res.baseline = True
        else:
            res.went_out, res.back_in, res.new_items, res.removed = diff(prev["products"], res.products)
        save_state(code, res.products, when)
    except Exception as exc:  # noqa: BLE001 - queremos reportar cualquier fallo
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# --------------------------------------------------------------------------------------
# Reporte
# --------------------------------------------------------------------------------------

def _line(code: str, sku: str, item: dict, old: dict | None = None, suffix: str = "") -> str:
    """Una línea del reporte con el link en formato de mercado; si el link cambió, muestra también el anterior."""
    lines = [f"- {item['name']} (SKU {sku}){suffix}"]
    if item.get("url"):
        new_link = market_link(item["url"], code)
        lines.append(f"  Link: {new_link}")
        if old and old.get("url"):
            old_link = market_link(old["url"], code)
            if old_link != new_link:
                lines.append(f"  Link anterior: {old_link}")
    return "\n".join(lines)


def build_report(results: list[MarketResult], today: str) -> str:
    out = [f"# Reporte de stock MONAT — {today}", ""]
    for r in results:
        cfg = MARKETS[r.code]
        out.append(f"## {cfg['name']} ({r.code})")
        if r.error:
            out += [f"⚠️ No se pudo completar el barrido: {r.error}", ""]
            continue
        if not r.verified:
            out.append("⚠️ No se pudo confirmar que el sitio estuviera en este mercado. No se compararon datos.")
            for name, (ok, detail) in r.checks.items():
                out.append(f"- {'✅' if ok else '❌'} {name}: {detail}")
            out.append("")
            continue
        total = len(r.products)
        n_out = sum(1 for v in r.products.values() if v["out"])
        out.append(f"Mercado verificado ✅ · {total} productos · {n_out} agotados")
        out.append("")
        if r.baseline:
            out.append("Primer barrido: se guardó como punto de partida. Productos agotados hoy:")
            out += [_line(r.code, k, v) for k, v in sorted(r.products.items(), key=lambda x: x[1]["name"].lower()) if v["out"]]
            out.append("")
            continue
        out.append(f"**Se agotaron ({len(r.went_out)})**")
        out += [_line(r.code, k, v, o) for k, v, o in r.went_out] or ["- Ninguno"]
        out.append("")
        out.append(f"**Volvieron a tener stock ({len(r.back_in)})**")
        out += [_line(r.code, k, v, o) for k, v, o in r.back_in] or ["- Ninguno"]
        out.append("")
        if r.new_items:
            out.append(f"Productos nuevos en el listado ({len(r.new_items)}):")
            out += [_line(r.code, k, v, suffix=" — agotado" if v["out"] else "") for k, v, _ in r.new_items]
            out.append("")
        if r.removed:
            out.append(f"Productos que ya no aparecen ({len(r.removed)}):")
            out += [_line(r.code, k, v) for k, v, _ in r.removed]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------------------
# Envío
# --------------------------------------------------------------------------------------

GCHAT_LIMIT = 3800  # Google Chat acepta hasta 4096 caracteres por mensaje; dejamos margen


def to_google_chat(report: str) -> str:
    """Adapta el Markdown del reporte al formato de Google Chat (*negrita*, sin #)."""
    lines = []
    for line in report.splitlines():
        if line.startswith("# "):
            line = f"*📦 {line[2:].strip()}*"
        elif line.startswith("## "):
            line = f"*{line[3:].strip()}*"
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        lines.append(line)
    return "\n".join(lines).strip()


def split_message(text: str, limit: int = GCHAT_LIMIT) -> list[str]:
    """Parte un texto largo en mensajes, sin cortar líneas ni separar un producto de su link."""
    blocks, current = [], []
    for line in text.splitlines():
        if line.startswith("  ") and current:  # línea de link: va pegada al producto anterior
            current[-1] += "\n" + line
        else:
            current.append(line)
    parts, buf = [], ""
    for block in current:
        candidate = f"{buf}\n{block}" if buf else block
        if len(candidate) > limit and buf:
            parts.append(buf)
            buf = block
        else:
            buf = candidate
    if buf:
        parts.append(buf)
    return parts


def send_google_chat(webhook: str, report: str, history_url: str | None, today: str) -> int:
    text = to_google_chat(report)
    if history_url:
        text += f"\n\n📁 Historial de esta semana: {history_url}"
    parts = split_message(text)
    for i, part in enumerate(parts, start=1):
        if len(parts) > 1:
            part = f"({i}/{len(parts)})\n{part}"
        # Todos los mensajes del mismo barrido van en el mismo hilo
        r = requests.post(
            webhook,
            params={"messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"},
            json={"text": part, "thread": {"threadKey": f"monat-stock-{today}"}},
            timeout=30,
        )
        r.raise_for_status()
    return len(parts)


def notify(report: str, subject: str, history_url: str | None = None, today: str = "") -> list[str]:
    sent = []
    gchat = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL")
    if gchat:
        n = send_google_chat(gchat, report, history_url, today)
        sent.append("Google Chat" + (f" ({n} mensajes)" if n > 1 else ""))
    slack = os.environ.get("SLACK_WEBHOOK_URL")
    if slack:
        requests.post(slack, json={"text": report}, timeout=30).raise_for_status()
        sent.append("Slack")
    teams = os.environ.get("TEAMS_WEBHOOK_URL")
    if teams:
        requests.post(teams, json={"text": report.replace("\n", "  \n")}, timeout=30).raise_for_status()
        sent.append("Teams")
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("MAIL_TO")
    if host and to:
        msg = MIMEText(report, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER", "")
        msg["To"] = to
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.sendmail(msg["From"], [a.strip() for a in to.split(",")], msg.as_string())
        sent.append("correo")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    return sent


# --------------------------------------------------------------------------------------
# Historial semanal
# --------------------------------------------------------------------------------------

def week_folder(today: str) -> Path:
    """historial/AAAA-MM-DD_Wnn — una carpeta por semana (si se repite en la semana, se sobrescribe)."""
    d = dt.date.fromisoformat(today)
    year, week, _ = d.isocalendar()
    monday = d - dt.timedelta(days=d.weekday())
    return HISTORY_DIR / f"{monday.isoformat()}_W{week:02d}"


def save_history(results: list[MarketResult], report: str, today: str) -> Path:
    folder = week_folder(today)
    # El primer barrido (punto de partida) se guarda aparte y nunca se sobrescribe,
    # aunque el workflow se vuelva a ejecutar esa misma semana.
    first = folder / "primer-barrido"
    if any(r.baseline for r in results) and not first.exists():
        _write_history(first, [r for r in results if r.baseline], report, today)
    _write_history(folder, results, report, today)
    return folder


def _write_history(folder: Path, results: list[MarketResult], report: str, today: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reporte.md").write_text(report, encoding="utf-8")
    for r in results:  # copia del estado de esa semana, solo de mercados verificados
        if r.verified and not r.error:
            (folder / f"estado_{r.code}.json").write_text(
                json.dumps({"scanned_on": today, "products": r.products}, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(dt.timezone(dt.timedelta(hours=-5))).strftime("%Y-%m-%d")
    codes = [c.strip().upper() for c in os.environ.get("MARKETS", "AU,NZ").split(",") if c.strip()]
    results = [run_market(c, MARKETS[c], now.isoformat()) for c in codes]

    report = build_report(results, today)
    folder = save_history(results, report, today)
    print(report)
    print(f"Historial guardado en {folder.relative_to(ROOT)}/")

    failed = [r.code for r in results if r.error or not r.verified]
    subject = f"MONAT stock {today}" + (f" — ⚠️ falló {', '.join(failed)}" if failed else "")
    try:
        history_url = None
        if os.environ.get("GITHUB_REPOSITORY"):
            server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
            branch = os.environ.get("GITHUB_REF_NAME", "main")
            history_url = f"{server}/{os.environ['GITHUB_REPOSITORY']}/tree/{branch}/{folder.relative_to(ROOT).as_posix()}"
        sent = notify(report, subject, history_url, today)
        print(f"Enviado por: {', '.join(sent) or 'ningún canal configurado'}")
    except Exception as exc:  # noqa: BLE001
        print(f"Error al enviar el mensaje: {exc}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
