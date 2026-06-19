import streamlit as st

from pohoda_xml import ProcessingError, generate_xml, load_orders

st.set_page_config(page_title="Amazon -> Pohoda banka (USD)", page_icon="💱", layout="centered")

st.title("💱 Amazon platby -> Pohoda bankové pohyby (XML)")
st.write(
    "Nahraj xlsx súbor s Amazon transakciami (chronologicky usporiadané, "
    "so stĺpcami `Transaction type`, `Total product charges`, `Amazon fees`, "
    "`Total (USD)`, `Kurz`, `Datum`, `Faktura`, `VS`). "
    "Spracujú sa len riadky s `Transaction type` = **Order Payment** "
    "- pre každú objednávku vzniknú 3 bankové pohyby (príjem, poplatok, netto úhrada)."
)

uploaded_file = st.file_uploader("Vyber xlsx súbor", type=["xlsx"])

col1, col2 = st.columns(2)
with col1:
    statement_number = st.number_input(
        "Číslo výpisu (bnk:statementNumber)", min_value=1, max_value=999, value=1, step=1
    )
with col2:
    start_movement = st.number_input(
        "Začiatočné číslo pohybu (bnk:numberMovement)", min_value=1, max_value=9999, value=1, step=1
    )

if uploaded_file is not None:
    try:
        df_orders, skipped = load_orders(uploaded_file)
    except ProcessingError as e:
        st.error(str(e))
        st.stop()

    if skipped:
        st.warning(
            f"⚠️ {skipped} riadok(ov) typu 'Order Payment' bolo preskočených "
            "kvôli chýbajúcim hodnotám (Kurz/Datum/Faktura/VS/sumy)."
        )

    st.success(f"Načítaných {len(df_orders)} objednávok (Order Payment) na spracovanie.")

    if st.button("🚀 Vygenerovať XML pre Pohodu", type="primary"):
        try:
            result = generate_xml(df_orders, int(statement_number), int(start_movement))
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
            f"{int(start_movement) + result.movement_count - 1:04d}"
        )

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
       obsahuje `bnk:symVar` aj `bnk:bankDetail` so symPar.
    2. **Výdaj** - suma = `abs(Amazon fees)`, predkontácia `Uhr.OZ-USD`,
       text "POPLATOK ZA TRANSAKCIU".
    3. **Výdaj** - suma = `Total (USD)`, predkontácia `Amaz.OZ-USD`,
       text "NETTO čIASTKKA FKSTURY" (ponechané presne podľa vzoru).
- Kurz = stĺpec `Kurz`. Suma v EUR (homeCurrency) = suma v USD / kurz, zaokrúhlené na 2 desatinné miesta.
- Dátum pohybu (`dateStatement`, `datePayment`) = stĺpec `Datum`.
- `bnk:symVar` / `bnk:symPar` sa odvodí zo stĺpca `VS` odstránením prvej číslice
  (overené, zhoduje sa s `Faktura`, napr. `2025VFB7` -> `20257`).
- `id` dataPacku = `USD` + dátumový rozsah spracovaných transakcií (`YYYYMMDD-YYYYMMDD`).
- `application` = `import`, `note` = `import platieb`. Atribúty `key` a `programVersion` sa vynechávajú.
- **`bnk:partnerIdentity`** (adresa zákazníka) sa **nevypĺňa**, keďže xlsx neobsahuje
  meno/adresu odberateľa - Pohoda by si ju mala dotiahnuť pri spárovaní podľa `symVar`
  s existujúcou vydanou faktúrou. Ak by si to chcel doplniť, treba do xlsx pridať stĺpce
  s adresou zákazníka a upraviť skript.
- Firemné údaje (EPPO BRANDS s.r.o., IČO, účet USDA...) sú nastavené ako konštanty na
  začiatku súboru `pohoda_xml.py` - uprav ich tam, ak by sa zmenili.
        """
    )
