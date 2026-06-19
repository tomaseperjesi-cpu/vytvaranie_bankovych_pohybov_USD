"""
Generovanie XML importu bankových pohybov pre Pohoda z Amazon platieb.

Logika:
- Spracúvajú sa len riadky, kde "Transaction type" == "Order Payment".
- Pre každú takú objednávku sa vytvoria 3 bankové pohyby (dataPackItem):
    1. PRÍJEM (receipt)  - suma = "Total product charges" (USD)
    2. VÝDAJ  (expense)  - suma = abs("Amazon fees") (kladná suma poplatku)
    3. VÝDAJ  (expense)  - suma = "Total (USD)" (netto suma faktúry)
- Kurz pre prepočet na EUR = stĺpec "Kurz".
- Dátum pohybu (dateStatement aj datePayment) = stĺpec "Datum".
- Variabilný symbol (symVar / symPar) sa odvodí zo stĺpca "VS" tak, že sa
  odstráni prvá ("1") číslica - napr. VS=120257 -> symVar=20257.
  (Overené na celom vzorovom súbore, zhoduje sa aj s "Faktura": 2025VFB7 -> 2025+7 = 20257.)
- ID dataPacku = "USD" + dátumový rozsah spracovaných transakcií (podľa stĺpca Datum).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

import pandas as pd

# ---------------------------------------------------------------------------
# KONŠTANTY - tu uprav, ak by sa zmenili firemné/účtovné údaje
# ---------------------------------------------------------------------------

ICO = "57039607"
ACCOUNT_IDS = "USDA"          # bnk:account/typ:ids a prefix bnk:number
CURRENCY = "USD"
SYM_CONST = "0308"

ACCOUNTING_RECEIPT = "úh.VFA USD"     # predkontácia pre 1. pohyb (príjem)
ACCOUNTING_FEE = "Uhr.OZ-USD"         # predkontácia pre 2. pohyb (poplatok)
ACCOUNTING_NET = "Amaz.OZ-USD"        # predkontácia pre 3. pohyb (netto)

TEXT_FEE = "POPLATOK ZA TRANSAKCIU"
TEXT_NET = "NETTO čIASTKKA FKSTURY"   # ponechané presne podľa vzorového súboru

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

REQUIRED_COLUMNS = [
    "Transaction type",
    "Total product charges",
    "Amazon fees",
    "Total (USD)",
    "Kurz",
    "Datum",
    "Faktura",
    "VS",
]

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


def parse_partner_lookup(file_obj) -> dict[str, str]:
    """Načíta export Vydaných faktúr z Pohody (inv:invoice) a vytvorí mapu
    symVar -> raw XML blok <typ:address ...>...</typ:address> (partnerIdentity),
    ktorý sa dá priamo vložiť do bankového pohybu."""
    raw = file_obj.read() if hasattr(file_obj, "read") else file_obj
    if isinstance(raw, str):
        raw = raw.encode("cp1250")
    encoding = _detect_xml_encoding(raw)
    text = raw.decode(encoding, errors="replace")

    lookup: dict[str, str] = {}
    for chunk in re.findall(r"<dat:dataPackItem.*?</dat:dataPackItem>", text, re.DOTALL):
        m_sym = re.search(r"<inv:symVar>(.*?)</inv:symVar>", chunk)
        m_partner = re.search(
            r"<inv:partnerIdentity>\s*(.*?)\s*</inv:partnerIdentity>", chunk, re.DOTALL
        )
        if not m_sym or not m_partner:
            continue
        sym = m_sym.group(1).strip()
        lookup[sym] = m_partner.group(1).strip()
    return lookup


class ProcessingError(Exception):
    pass


@dataclass
class RunResult:
    xml_bytes: bytes
    filename: str
    order_count: int
    movement_count: int
    skipped_rows: int
    date_min: str
    date_max: str
    pack_id: str
    preview_xml: str
    partner_matched: int = 0
    partner_missing_fakturas: list[str] = field(default_factory=list)


def _round2(value) -> Decimal:
    return Decimal(str(round(float(value), 2))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _round4(value) -> Decimal:
    return Decimal(str(round(float(value), 4))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _fmt_amount(d: Decimal) -> str:
    """Formátuje sumu/kurz tak, aby zbytočné nuly na konci boli orezané,
    rovnako ako to robí vzorový XML súbor (napr. 2.40 -> '2.4')."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _symvar_from_vs(vs) -> str:
    s = str(int(float(vs)))
    return s[1:] if len(s) > 1 else s


def _pad(value: int, width: int) -> str:
    return str(value).zfill(width)


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
    include_detail: bool,
    partner_block: str | None = None,
) -> str:
    number = f"{ACCOUNT_IDS}{statement_number}{movement_number}"
    home_amount = (foreign_amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    symvar_line = f"\n\t\t\t\t<bnk:symVar>{sym_var}</bnk:symVar>" if sym_var else ""

    partner_xml = ""
    if partner_block:
        partner_xml = (
            f"\n\t\t\t\t<bnk:partnerIdentity>\n\t\t\t\t\t{partner_block}"
            f"\n\t\t\t\t</bnk:partnerIdentity>"
        )

    detail_block = ""
    if include_detail:
        detail_block = f"""
\t\t\t<bnk:bankDetail {NS_COMMON}>
\t\t\t\t<bnk:bankItem>
\t\t\t\t\t<bnk:text>{escape(text)}</bnk:text>
\t\t\t\t\t<bnk:homeCurrency>
\t\t\t\t\t\t<bnk:unitPrice>{_fmt_amount(home_amount)}</bnk:unitPrice>
\t\t\t\t\t</bnk:homeCurrency>
\t\t\t\t\t<bnk:foreignCurrency>
\t\t\t\t\t\t<bnk:unitPrice>{_fmt_amount(foreign_amount)}</bnk:unitPrice>
\t\t\t\t\t</bnk:foreignCurrency>
\t\t\t\t\t<bnk:symPar>{sym_var}</bnk:symPar>
\t\t\t\t</bnk:bankItem>
\t\t\t</bnk:bankDetail>"""

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
\t\t\t\t</bnk:statementNumber>{symvar_line}
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
\t\t\t</bnk:bankHeader>{detail_block}
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


def load_orders(file_or_path) -> pd.DataFrame:
    df = pd.read_excel(file_or_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ProcessingError(
            "V súbore chýbajú očakávané stĺpce: " + ", ".join(missing)
        )

    op_all = df[df["Transaction type"] == "Order Payment"].copy()
    needed = [
        "Total product charges",
        "Amazon fees",
        "Total (USD)",
        "Kurz",
        "Datum",
        "Faktura",
        "VS",
    ]
    op = op_all.dropna(subset=needed).copy()
    skipped = len(op_all) - len(op)
    return op, skipped


def generate_xml(
    df_orders: pd.DataFrame,
    statement_number: int,
    start_movement: int,
    partner_lookup: dict[str, str] | None = None,
) -> RunResult:
    if df_orders.empty:
        raise ProcessingError("Nenašli sa žiadne riadky 'Order Payment' s kompletnými údajmi.")

    partner_lookup = partner_lookup or {}

    dates = pd.to_datetime(df_orders["Datum"])
    date_min, date_max = dates.min(), dates.max()
    pack_id = f"{CURRENCY}{date_min.strftime('%Y%m%d')}-{date_max.strftime('%Y%m%d')}"

    statement_str = _pad(statement_number, 3)
    movement = int(start_movement)

    items_xml: list[str] = []
    idx = 1
    partner_matched = 0
    partner_missing_fakturas: list[str] = []
    for _, row in df_orders.iterrows():
        date_str = pd.Timestamp(row["Datum"]).strftime("%Y-%m-%d")
        rate = _round4(row["Kurz"])
        faktura = str(row["Faktura"])
        vs_raw = str(int(float(row["VS"])))
        sym_var = _symvar_from_vs(row["VS"])
        text_receipt = f"Úhrada FV č. {faktura}"

        partner_block = partner_lookup.get(vs_raw)
        if partner_block:
            partner_matched += 1
        else:
            partner_missing_fakturas.append(faktura)

        amt_receipt = _round2(row["Total product charges"])
        amt_fee = _round2(abs(float(row["Amazon fees"])))
        amt_net = _round2(row["Total (USD)"])

        mv = _pad(movement, 4)
        items_xml.append(
            _build_item(
                pack_id, idx, "receipt", statement_str, mv, date_str,
                ACCOUNTING_RECEIPT, text_receipt, sym_var,
                amt_receipt, rate, include_detail=True,
                partner_block=partner_block,
            )
        )
        movement += 1
        idx += 1

        mv = _pad(movement, 4)
        items_xml.append(
            _build_item(
                pack_id, idx, "expense", statement_str, mv, date_str,
                ACCOUNTING_FEE, TEXT_FEE, None,
                amt_fee, rate, include_detail=False,
            )
        )
        movement += 1
        idx += 1

        mv = _pad(movement, 4)
        items_xml.append(
            _build_item(
                pack_id, idx, "expense", statement_str, mv, date_str,
                ACCOUNTING_NET, TEXT_NET, None,
                amt_net, rate, include_detail=False,
            )
        )
        movement += 1
        idx += 1

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

    filename = f"Banka_Eppo_{pack_id}.xml"

    return RunResult(
        xml_bytes=xml_bytes,
        filename=filename,
        order_count=len(df_orders),
        movement_count=len(items_xml),
        skipped_rows=0,
        date_min=date_min.strftime("%d.%m.%Y"),
        date_max=date_max.strftime("%d.%m.%Y"),
        pack_id=pack_id,
        preview_xml=full_xml,
        partner_matched=partner_matched,
        partner_missing_fakturas=partner_missing_fakturas,
    )
