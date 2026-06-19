import streamlit as st

from pohoda_xml import ProcessingError, generate_xml, load_orders, parse_invoice_lookup

st.set_page_config(page_title="Amazon -> Pohoda banka (USD)", page_icon="💱", layout="centered")

st.title("💱 Amazon platby -> Pohoda bankové pohyby (XML)")
st.write(
    "1) Nahraj xlsx súbor s Amazon transakciami (stĺpce `Transaction type`, "
    "`Total product charges`, `Amazon fees`, `Total (USD)`, `Kurz`, `Datum`, `Faktura`).\n\n"
    "2) Nahraj XML export **Vydaných faktúr z Pohody** (`inv:invoice` formát) - "
    "z neho sa preberá **skutočný variabilný symbol** (`inv:symVar`) a adresa zákazníka "
    "(`inv:partnerIdentity`) podľa čísla faktúry. Tento súbor je **povinný**, keďže "
    "variabilný symbol sa už neodvodzuje, ale berie priamo z Pohody.\n\n"
    "Spracujú sa len riadky s `Transaction type` = **Order Payment** "
    "- pre každú objednávku vzniknú 3 bankové pohyby (príjem, poplatok, netto úhrada)."
)

col_u1, col_u2 = st.columns(2)
with col_u1:
    payments_file = st.file_uploader("1) Platby z Amazonu (xlsx)", type=["xlsx"])
with col_u2:
    invoices_file = st.file_uploader(
        "2) Vydané faktúry z Pohody (xml) - povinné",
        type=["xml"],
    )

col1, col2 = st.columns(2)
with col1:
    statement_number = st.number_input(
        "Číslo výpisu (bnk:statementNumber)", min_value=1, max_value=999, value=1, step=1
    )
with col2:
    start_movement = st.number_input(
        "Začiatočné číslo pohybu (bnk:numberMovement)", min_value=1, max_value=9999, value=1, step=1
    )

if payments_file is not None:
    try:
        df_orders, skipped = load_orders(payments_file)
    except ProcessingError as e:
        st.error(str(e))
        st.stop()

    if skipped:
        st.warning(
            f"⚠️ {skipped} riadok(ov) typu 'Order Payment' bolo preskočených "
            "kvôli chýbajúcim hodnotám (Kurz/Datum/Faktura/VS/sumy)."
        )

    st.success(f"Načítaných {len(df_orders)} objednávok (Order Payment) na spracovanie.")

    invoice_lookup = {}
    if invoices_file is not None:
        try:
            invoice_lookup = parse_invoice_lookup(invoices_file)
            st.info(f"📇 Z faktúr načítaných {len(invoice_lookup)} záznamov (podľa čísla faktúry).")
        except Exception as e:
            st.error(f"Nepodarilo sa spracovať súbor s faktúrami: {e}")
            st.stop()
    else:
        st.warning("⚠️ Najprv nahraj XML export Vydaných faktúr z Pohody (povinné pole 2).")

    if st.button("🚀 Vygenerovať XML pre Pohodu", type="primary", disabled=invoices_file is None):
        try:
            result = generate_xml(
                df_orders, int(statement_number), int(start_movement), invoice_lookup
            )
        except ProcessingError as e:
            st.error(str(e))
            st.stop()

        st.success(
            f"Hotovo! Vygenerovaných **{result.movement_count}** bankových pohybov "
            f"pre **{result.order_count}** objednávok.\n\n"
            f"- Dátumový rozsah: {result.date_min} - {result.date_max}\n"
            f"- ID dataPacku: `{result.pack_id}`\n"
            f"- Číslo výpisu: {int(statement_number):03d}, "
            f"pohyby: {int(start_movement):04d} - "
            f"{int(start_movement) + result.movement_count - 1:04d}\n"
            f"- Adresa zákazníka napárovaná: {result.partner_matched} / {result.order_count}"
        )

        if result.unmatched_fakturas:
            with st.expander(
                f"❌ {len(result.unmatched_fakturas)} objednávok BEZ faktúry v XML exporte "
                "- pre tieto sa pohyby nevytvorili"
            ):
                st.write(", ".join(result.unmatched_fakturas))

        if result.missing_partner_fakturas:
            with st.expander(
                f"⚠️ {len(result.missing_partner_fakturas)} faktúr bez adresy zákazníka "
                "(symVar sa použil, partnerIdentity sa vynechalo)"
            ):
                st.write(", ".join(result.missing_partner_fakturas))

        st.download_button(
            label="⬇️ Stiahnuť XML pre import do Pohody",
            data=result.xml_bytes,
            file_name=result.filename,
            mime="application/xml",
        )

        with st.expander("Náhľad vygenerovaného XML (prvé 3 pohyby)"):
            preview = result.preview_xml.split("</dat:dataPackItem>")
            preview_text = "</dat:dataPackItem>".join(preview[:3]) + "</dat:dataPackItem>"
            st.code(preview_text, language="xml")

st.divider()
with st.expander("ℹ️ Logika spracovania / poznámky"):
    st.markdown(
        """
- Spracúvajú sa **len** riadky s `Transaction type` = `Order Payment`.
- Pre každú objednávku vznikajú 3 pohyby (presne podľa vzoru *Banka_Eppo_USD_1.xml*):
    1. **Príjem** - suma = `Total product charges` (USD), predkontácia `úh.VFA USD`,
       obsahuje `bnk:symVar`, `bnk:partnerIdentity` (ak sa napárovala adresa) aj `bnk:bankDetail` so symPar.
    2. **Výdaj** - suma = `abs(Amazon fees)`, predkontácia `Uhr.OZ-USD`,
       text "POPLATOK ZA TRANSAKCIU".
    3. **Výdaj** - suma = `Total (USD)`, predkontácia `Amaz.OZ-USD`,
       text "NETTO čIASTKKA FKSTURY" (ponechané presne podľa vzoru).
- Kurz = stĺpec `Kurz`. Suma v EUR (homeCurrency) = suma v USD / kurz, zaokrúhlené na 2 desatinné miesta.
- Dátum pohybu (`dateStatement`, `datePayment`) = stĺpec `Datum`.
- **`bnk:symVar` aj `bnk:symPar` sa preberajú priamo z XML exportu Vydaných faktúr**
  (`inv:symVar` napárovaný podľa čísla faktúry `Faktura` <-> `typ:numberRequested`) -
  nie je to odvodené ani upravované, je to presne tá hodnota, akú má faktúra v Pohode.
- `bnk:partnerIdentity` (adresa zákazníka) sa tiež berie z toho istého napárovaného záznamu faktúry.
- Ak sa faktúra pre danú objednávku v XML exporte nenájde, pohyby sa pre ňu **nevytvoria**
  (zobrazí sa zoznam vynechaných objednávok) - keďže bez reálneho VS z Pohody nie je čo vložiť.
- `id` dataPacku = `USD` + dátumový rozsah spracovaných transakcií (`YYYYMMDD-YYYYMMDD`).
- `application` = `import`, `note` = `import platieb`. Atribúty `key` a `programVersion` sa vynechávajú.
- Firemné údaje (EPPO BRANDS s.r.o., IČO, účet USDA...) sú nastavené ako konštanty na
  začiatku súboru `pohoda_xml.py` - uprav ich tam, ak by sa zmenili.
        """
    )
