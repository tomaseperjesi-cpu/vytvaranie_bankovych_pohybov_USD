"""
Generovanie XML importu bankových pohybov pre Pohoda z Amazon platieb.

Spracúvané typy transakcií:
- Order Payment  → 3 pohyby (príjem, poplatok, netto)
- Service Fees   → 1 pohyb  (výdaj, text "Service Fees")
- Refund         → 3 pohyby ako Order Payment, hodnoty záporné (Amazon fees
                   sa explicitne zápornuje, ostatné dva stĺpce sú už záporné
                   v zdrojových dátach)

Dátum: primárne stĺpec "Datum", záložne stĺpec "Date".
Kurz:  stĺpec "Kurz"; ak chýba (Service Fees, Refund), nastaví sa rate=0
       a Pohoda doplní kurz automaticky z kurzového lístka NBS/ECB podľa dátumu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

import pandas as pd

# ---------------------------------------------------------------------------
# KONŠTANTY
# ---------------------------------------------------------------------------

ICO = "57039607"
ACCOUNT_IDS = "USDA"
CURRENCY = "USD"
SYM_CONST = "0308"

ACCOUNTING_RECEIPT = "úh.VFA USD"
ACCOUNTING_FEE = "Uhr.OZ-USD"
ACCOUNTING_NET = "Amaz.OZ-USD"

TEXT_FEE = "POPLATOK ZA TRANSAKCIU"
TEXT_NET = "NETTO čIASTKKA FKSTURY"
TEXT_SERVICE_FEE = "Service Fees"

APPLICATION = "import"
NOTE = "import platieb"

MY_COMPANY = {
    "company": "EPPO BRANDS s. r. o.",
    "city": "Zvolen",
    "street": "Tulská",
    "number": "9386/6B",
    "zip": "960 01",
    "ico": ICO,
    "dic": "2122546481",
    "icdph": "SK2122546481",
}

REQUIRED_COLUMNS_BASE = ["Transaction type", "Total (USD)"]

NS_COMMON = (
    'xmlns:rsp="http://www.stormware.cz/schema/version_2/response.xsd" '
    'xmlns:rdc="http://www.stormware.cz/schema/version_2/documentresponse.xsd" '
    'xmlns:typ="http://www.stormware.cz/schema/version_2/type.xsd" '
    'xmlns:ftr="http://www.stormware.cz/schema/version_2/filter.xsd" '
    'xmlns:lst="http://www.stormware.cz/schema/version_2/list.xsd"'
)

MY_IDENTITY_BLOCK = f"""\t\t\t\t<bnk:myIdentity>
\t\t\t\t\t<typ:address>
\t\t\t\t\t\t<typ:company>{escape(MY_COMPANY['company'])}</typ:company>
\t\t\t\t\t\t<typ:city>{escape(MY_COMPANY['city'])}</typ:city>
\t\t\t\t\t\t<typ:street>{escape(MY_COMPANY['street'])}</typ:street>
\t\t\t\t\t\t<typ:number>{escape(MY_COMPANY['number'])}</typ:number>
\t\t\t\t\t\t<typ:zip>{escape(MY_COMPANY['zip'])}</typ:zip>
\t\t\t\t\t\t<typ:ico>{MY_COMPANY['ico']}</typ:ico>
\t\t\t\t\t\t<typ:dic>{MY_COMPANY['dic']}</typ:dic>
\t\t\t\t\t\t<typ:icDph>{MY_COMPANY['icdph']}</typ:icDph>
\t\t\t\t\t</typ:address>
\t\t\t\t</bnk:myIdentity>"""


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def _detect_xml_encoding(raw: bytes) -> str:
    head = raw[:200].decode("ascii", errors="ignore")
    m = re.search(r'encoding=[\'"]([\w-]+)[\'"]', head)
    if m:
        enc = m.group(1)
        try:
            "test".encode(enc)
            return enc
        except LookupError:
            pass
    return "cp1250"


def _round2(value) -> Decimal:
    return Decimal(str(round(float(value), 2))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _round4(value) -> Decimal:
    return Decimal(str(round(float(value), 4))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _fmt_amount(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _pad(value: int, width: int) -> str:
    return str(value).zfill(width)


def _get_date(row) -> str:
    """Vráti dátum pohybu: primárne stĺpec Datum, záložne stĺpec Date."""
    datum = row.get("Datum") if "Datum" in row.index else None
    if pd.notna(datum):
        return pd.Timestamp(datum).strftime("%Y-%m-%d")
    date_col = row.get("Date") if "Date" in row.index else None
    if pd.notna(date_col):
        return pd.Timestamp(date_col).strftime("%Y-%m-%d")
    raise ValueError(f"Riadok nemá ani Datum ani Date: {row.get('Transaction type')}")


def _get_kurz(row) -> Decimal:
    """Vráti kurz zo stĺpca Kurz; ak chýba, vráti 0 — Pohoda doplní z NBS/ECB."""
    kurz = row.get("Kurz") if "Kurz" in row.index else None
    if pd.notna(kurz):
        return _round4(kurz)
    return Decimal("0")


# ---------------------------------------------------------------------------
# INVOICE LOOKUP
# ---------------------------------------------------------------------------

@dataclass
class InvoiceInfo:
    sym_var: str
    partner_block: str | None


def parse_invoice_lookup(file_obj) -> dict[str, InvoiceInfo]:
    """Načíta export Vydaných faktúr z Pohody → mapa číslo faktúry → InvoiceInfo."""
    raw = file_obj.read() if hasattr(file_obj, "read") else file_obj
    if isinstance(raw, str):
        raw = raw.encode("cp1250")
    encoding = _detect_xml_encoding(raw)
    text = raw.decode(encoding, errors="replace")

    lookup: dict[str, InvoiceInfo] = {}
    for chunk in re.findall(r"<dat:dataPackItem.*?</dat:dataPackItem>", text, re.DOTALL):
        m_num = re.search(r"<typ:numberRequested>(.*?)</typ:numberRequested>", chunk)
        m_sym = re.search(r"<inv:symVar>(.*?)</inv:symVar>", chunk)
        if not m_num or not m_sym:
            continue
        number = m_num.group(1).strip()
        sym_var = m_sym.group(1).strip()
        m_partner = re.search(
            r"<inv:partnerIdentity>\s*(.*?)\s*</inv:partnerIdentity>", chunk, re.DOTALL
        )
        partner_block = m_partner.group(1).strip() if m_partner else None
        lookup[number] = InvoiceInfo(sym_var=sym_var, partner_block=partner_block)
    return lookup


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------

class ProcessingError(Exception):
    pass


def load_orders(file_or_path):
    """Načíta xlsx a vráti (df_orders, df_service_fees, df_refunds, skipped_count).

    - df_orders:       riadky 'Order Payment'
    - df_service_fees: riadky 'Service Fees'
    - df_refunds:      riadky 'Refund'
    - skipped_count:   počet riadkov preskočených pre chýbajúce hodnoty
    """
    df = pd.read_excel(file_or_path)

    # Minimálne povinné stĺpce pre celý súbor
    missing_base = [c for c in REQUIRED_COLUMNS_BASE if c not in df.columns]
    if missing_base:
        raise ProcessingError(
            "V súbore chýbajú povinné stĺpce: " + ", ".join(missing_base)
        )

    # --- Order Payment ---
    op_needed = ["Total product charges", "Amazon fees", "Total (USD)", "Faktura"]
    op_all = df[df["Transaction type"] == "Order Payment"].copy()
    op_available = [c for c in op_needed if c in df.columns]
    op = op_all.dropna(subset=op_available).copy()

    # --- Service Fees (Kurz zvyčajne chýba → rate=0, Pohoda doplní z NBS/ECB) ---
    sf_all = df[df["Transaction type"] == "Service Fees"].copy()
    sf = sf_all.dropna(subset=["Total (USD)"]).copy()

    # --- Refund ---
    rf_needed = ["Total product charges", "Amazon fees", "Total (USD)"]
    rf_all = df[df["Transaction type"] == "Refund"].copy()
    rf_available = [c for c in rf_needed if c in df.columns]
    rf = rf_all.dropna(subset=rf_available).copy()

    skipped = (len(op_all) - len(op)) + (len(sf_all) - len(sf)) + (len(rf_all) - len(rf))
    return op, sf, rf, skipped


# ---------------------------------------------------------------------------
# XML ITEM BUILDER
# ---------------------------------------------------------------------------

def _build_item(
    pack_id: str,
    item_index: int,
    bank_type: str,
    statement_number: str,
    movement_number: str,
    date_str: str,
    accounting_id: str,
    text: str,
    sym_var: str | None,
    foreign_amount: Decimal,
    rate: Decimal,
    partner_block: str | None = None,
    sym_par: str | None = None,
) -> str:
    number = f"{ACCOUNT_IDS}{statement_number}{movement_number}"
    # rate=0 znamená "Pohoda doplní kurz z NBS/ECB podľa dátumu"
    home_amount = (
        (foreign_amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if rate != Decimal("0")
        else Decimal("0")
    )

    symvar_line = f"\n\t\t\t\t<bnk:symVar>{sym_var}</bnk:symVar>" if sym_var else ""
    sympar_line = (
        f"\n\t\t\t\t<bnk:symPar>{sym_par if sym_par else sym_var}</bnk:symPar>"
        if (sym_par or sym_var) else ""
    )

    partner_xml = ""
    if partner_block:
        partner_xml = (
            f"\n\t\t\t\t<bnk:partnerIdentity>\n\t\t\t\t\t{partner_block}"
            f"\n\t\t\t\t</bnk:partnerIdentity>"
        )

    return f"""\t<dat:dataPackItem version="2.0" id="{escape(pack_id)} ({item_index:03d})">
\t\t<bnk:bank version="2.0" xmlns:bnk="http://www.stormware.cz/schema/version_2/bank.xsd">
\t\t\t<bnk:bankHeader {NS_COMMON}>
\t\t\t\t<bnk:bankType>{bank_type}</bnk:bankType>
\t\t\t\t<bnk:account>
\t\t\t\t\t<typ:ids>{ACCOUNT_IDS}</typ:ids>
\t\t\t\t</bnk:account>
\t\t\t\t<bnk:number>{number}</bnk:number>
\t\t\t\t<bnk:statementNumber>
\t\t\t\t\t<bnk:statementNumber>{statement_number}</bnk:statementNumber>
\t\t\t\t\t<bnk:numberMovement>{movement_number}</bnk:numberMovement>
\t\t\t\t</bnk:statementNumber>{symvar_line}{sympar_line}
\t\t\t\t<bnk:dateStatement>{date_str}</bnk:dateStatement>
\t\t\t\t<bnk:datePayment>{date_str}</bnk:datePayment>
\t\t\t\t<bnk:accounting>
\t\t\t\t\t<typ:ids>{escape(accounting_id)}</typ:ids>
\t\t\t\t</bnk:accounting>
\t\t\t\t<bnk:text>{escape(text)}</bnk:text>{partner_xml}
{MY_IDENTITY_BLOCK}
\t\t\t\t<bnk:symConst>{SYM_CONST}</bnk:symConst>
\t\t\t\t<bnk:lock2>false</bnk:lock2>
\t\t\t\t<bnk:markRecord>true</bnk:markRecord>
\t\t\t</bnk:bankHeader>
\t\t\t<bnk:bankSummary {NS_COMMON}>
\t\t\t\t<bnk:roundingDocument>none</bnk:roundingDocument>
\t\t\t\t<bnk:roundingVAT>none</bnk:roundingVAT>
\t\t\t\t<bnk:homeCurrency>
\t\t\t\t\t<typ:priceNone>{_fmt_amount(home_amount)}</typ:priceNone>
\t\t\t\t\t<typ:priceLow>0</typ:priceLow>
\t\t\t\t\t<typ:priceLowVAT>0</typ:priceLowVAT>
\t\t\t\t\t<typ:priceLowSum>0</typ:priceLowSum>
\t\t\t\t\t<typ:priceHigh>0</typ:priceHigh>
\t\t\t\t\t<typ:priceHighVAT>0</typ:priceHighVAT>
\t\t\t\t\t<typ:priceHighSum>0</typ:priceHighSum>
\t\t\t\t\t<typ:price3>0</typ:price3>
\t\t\t\t\t<typ:price3VAT>0</typ:price3VAT>
\t\t\t\t\t<typ:price3Sum>0</typ:price3Sum>
\t\t\t\t\t<typ:round>
\t\t\t\t\t\t<typ:priceRound>0</typ:priceRound>
\t\t\t\t\t</typ:round>
\t\t\t\t</bnk:homeCurrency>
\t\t\t\t<bnk:foreignCurrency>
\t\t\t\t\t<typ:currency>
\t\t\t\t\t\t<typ:ids>{CURRENCY}</typ:ids>
\t\t\t\t\t</typ:currency>
\t\t\t\t\t<typ:rate>{_fmt_amount(rate)}</typ:rate>
\t\t\t\t\t<typ:amount>1</typ:amount>
\t\t\t\t\t<typ:priceSum>{_fmt_amount(foreign_amount)}</typ:priceSum>
\t\t\t\t\t<typ:round>
\t\t\t\t\t\t<typ:priceRound>0</typ:priceRound>
\t\t\t\t\t</typ:round>
\t\t\t\t</bnk:foreignCurrency>
\t\t\t</bnk:bankSummary>
\t\t</bnk:bank>
\t</dat:dataPackItem>"""


# ---------------------------------------------------------------------------
# RESULT
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    xml_bytes: bytes
    filename: str
    order_count: int
    service_fee_count: int
    refund_count: int
    movement_count: int
    skipped_rows: int
    date_min: str
    date_max: str
    pack_id: str
    preview_xml: str
    partner_matched: int = 0
    unmatched_fakturas: list[str] = field(default_factory=list)
    missing_partner_fakturas: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MAIN GENERATE FUNCTION
# ---------------------------------------------------------------------------

def generate_xml(
    df_orders: pd.DataFrame,
    df_service_fees: pd.DataFrame,
    df_refunds: pd.DataFrame,
    statement_number: int,
    start_movement: int,
    invoice_lookup: dict[str, InvoiceInfo] | None = None,
) -> RunResult:

    invoice_lookup = invoice_lookup or {}

    total_rows = len(df_orders) + len(df_service_fees) + len(df_refunds)
    if total_rows == 0:
        raise ProcessingError("Nenašli sa žiadne spracovateľné riadky (Order Payment / Service Fees / Refund).")

    if not df_orders.empty and not invoice_lookup:
        raise ProcessingError(
            "Chýba XML export Vydaných faktúr z Pohody — variabilný symbol sa berie "
            "výhradne z neho, takže je tento súbor povinný pre Order Payment riadky."
        )

    # Kombinujeme všetky typy a zoradíme podľa dátumu
    frames = []
    if not df_orders.empty:
        tmp = df_orders.copy(); tmp["_type"] = "order"; frames.append(tmp)
    if not df_service_fees.empty:
        tmp = df_service_fees.copy(); tmp["_type"] = "servicefee"; frames.append(tmp)
    if not df_refunds.empty:
        tmp = df_refunds.copy(); tmp["_type"] = "refund"; frames.append(tmp)

    df_all = pd.concat(frames)

    # Dátum pre triedenie: Datum ak dostupný, inak Date
    def _sort_date(row):
        d = row.get("Datum") if "Datum" in row.index else None
        if pd.notna(d):
            return pd.Timestamp(d)
        d2 = row.get("Date") if "Date" in row.index else None
        if pd.notna(d2):
            return pd.Timestamp(d2)
        return pd.Timestamp("2099-01-01")

    df_all["_sort_date"] = df_all.apply(_sort_date, axis=1)
    df_all = df_all.sort_values("_sort_date").reset_index(drop=True)

    date_min = df_all["_sort_date"].min()
    date_max = df_all["_sort_date"].max()
    pack_id = f"{CURRENCY}{date_min.strftime('%Y%m%d')}-{date_max.strftime('%Y%m%d')}"

    statement_str = _pad(statement_number, 3)
    movement = int(start_movement)

    items_xml: list[str] = []
    idx = 1
    order_count = 0
    service_fee_count = 0
    refund_count = 0
    partner_matched = 0
    unmatched_fakturas: list[str] = []
    missing_partner_fakturas: list[str] = []

    for _, row in df_all.iterrows():
        tx_type = row["_type"]

        # Dátum
        try:
            date_str = _get_date(row)
        except ValueError:
            continue  # riadok bez dátumu preskočíme (nemal by nastať)

        # Kurz — 0 znamená "Pohoda doplní z NBS/ECB"
        rate = _get_kurz(row)

        # ── Order Payment ─────────────────────────────────────────────────
        if tx_type == "order":
            faktura = str(row["Faktura"])
            info = invoice_lookup.get(faktura)
            if info is None:
                unmatched_fakturas.append(faktura)
                continue

            sym_var = info.sym_var
            text_receipt = f"Úhrada FV č. {faktura}"

            if info.partner_block:
                partner_matched += 1
            else:
                missing_partner_fakturas.append(faktura)

            amt_receipt = _round2(row["Total product charges"])
            amt_fee     = _round2(abs(float(row["Amazon fees"])))
            amt_net     = _round2(row["Total (USD)"])

            mv = _pad(movement, 4)
            items_xml.append(_build_item(
                pack_id, idx, "receipt", statement_str, mv, date_str,
                ACCOUNTING_RECEIPT, text_receipt, sym_var, amt_receipt, rate,
                partner_block=info.partner_block, sym_par=sym_var,
            ))
            movement += 1; idx += 1

            mv = _pad(movement, 4)
            items_xml.append(_build_item(
                pack_id, idx, "expense", statement_str, mv, date_str,
                ACCOUNTING_FEE, TEXT_FEE, None, amt_fee, rate,
            ))
            movement += 1; idx += 1

            mv = _pad(movement, 4)
            items_xml.append(_build_item(
                pack_id, idx, "expense", statement_str, mv, date_str,
                ACCOUNTING_NET, TEXT_NET, None, amt_net, rate,
            ))
            movement += 1; idx += 1

            order_count += 1

        # ── Service Fees ──────────────────────────────────────────────────
        elif tx_type == "servicefee":
            amt_sf = _round2(abs(float(row["Total (USD)"])))

            mv = _pad(movement, 4)
            items_xml.append(_build_item(
                pack_id, idx, "expense", statement_str, mv, date_str,
                ACCOUNTING_FEE, TEXT_SERVICE_FEE, None, amt_sf, rate,
            ))
            movement += 1; idx += 1

            service_fee_count += 1

        # ── Refund ────────────────────────────────────────────────────────
        elif tx_type == "refund":
            faktura_val = row.get("Faktura") if "Faktura" in row.index else None
            faktura = str(faktura_val) if pd.notna(faktura_val) else None

            info = invoice_lookup.get(faktura) if faktura else None
            sym_var      = info.sym_var      if info else None
            partner_block = info.partner_block if info else None

            if info and info.partner_block:
                partner_matched += 1
            elif info and not info.partner_block and faktura:
                missing_partner_fakturas.append(f"[Refund] {faktura}")

            text_refund = f"Refund FV č. {faktura}" if faktura else "Refund"

            # Hodnoty sa berú as-is (záporné pre product charges a total).
            # Amazon fees je v zdrojových dátach pri Refund kladné číslo, preto
            # ho treba explicitne zápornúť, aby bol tento pohyb tiež vo výdaji
            # zaúčtovaný so záporným znamienkom (rovnako ako ostatné dva pohyby).
            amt_receipt = _round2(float(row["Total product charges"]))
            amt_fee     = _round2(-abs(float(row["Amazon fees"])))
            amt_net     = _round2(float(row["Total (USD)"]))

            mv = _pad(movement, 4)
            items_xml.append(_build_item(
                pack_id, idx, "receipt", statement_str, mv, date_str,
                ACCOUNTING_RECEIPT, text_refund, sym_var, amt_receipt, rate,
                partner_block=partner_block, sym_par=sym_var,
            ))
            movement += 1; idx += 1

            mv = _pad(movement, 4)
            items_xml.append(_build_item(
                pack_id, idx, "expense", statement_str, mv, date_str,
                ACCOUNTING_FEE, TEXT_FEE, None, amt_fee, rate,
            ))
            movement += 1; idx += 1

            mv = _pad(movement, 4)
            items_xml.append(_build_item(
                pack_id, idx, "expense", statement_str, mv, date_str,
                ACCOUNTING_NET, TEXT_NET, None, amt_net, rate,
            ))
            movement += 1; idx += 1

            refund_count += 1

    if not items_xml:
        raise ProcessingError(
            "Žiadny riadok sa nepodarilo spracovať. Skontroluj vstupné súbory a kurz."
        )

    body = "\n".join(items_xml)
    full_xml = (
        '<?xml version="1.0" encoding="Windows-1250"?>\r\n'
        f'<dat:dataPack version="2.0" id="{escape(pack_id)}" ico="{ICO}" '
        f'application="{APPLICATION}" note="{escape(NOTE)}" '
        'xmlns:dat="http://www.stormware.cz/schema/version_2/data.xsd">\r\n'
        + body.replace("\n", "\r\n")
        + "\r\n</dat:dataPack>\r\n"
    )

    xml_bytes = full_xml.encode("cp1250", errors="xmlcharrefreplace")

    end_movement = movement - 1
    filename = (
        f"Banka_Eppo_{CURRENCY}_"
        f"{statement_str}_"
        f"{_pad(int(start_movement), 4)}-{_pad(end_movement, 4)}_"
        f"{date_min.strftime('%Y%m%d')}-{date_max.strftime('%Y%m%d')}.xml"
    )

    return RunResult(
        xml_bytes=xml_bytes,
        filename=filename,
        order_count=order_count,
        service_fee_count=service_fee_count,
        refund_count=refund_count,
        movement_count=len(items_xml),
        skipped_rows=0,
        date_min=date_min.strftime("%d.%m.%Y"),
        date_max=date_max.strftime("%d.%m.%Y"),
        pack_id=pack_id,
        preview_xml=full_xml,
        partner_matched=partner_matched,
        unmatched_fakturas=unmatched_fakturas,
        missing_partner_fakturas=missing_partner_fakturas,
    )
