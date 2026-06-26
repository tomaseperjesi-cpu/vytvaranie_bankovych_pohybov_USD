import streamlit as st

from pohoda_xml import ProcessingError, generate_xml, load_orders, parse_invoice_lookup

st.set_page_config(page_title="Amazon -> Pohoda banka (USD)", page_icon="💱", layout="centered")

st.title("💱 Amazon platby -> Pohoda bankové pohyby (XML)")
st.write(
    "1) Nahraj xlsx súbor s Amazon transakciami. Spracúvajú sa 3 typy:\n\n"
    "- **Order Payment** → 3 pohyby (príjem, poplatok, netto)\n"
    "- **Service Fees** → 1 pohyb (výdaj)\n"
    "- **Refund** → 3 pohyby so zápornými hodnotami\n\n"
    "2) Nahraj XML export **Vydaných faktúr z Pohody** — povinný pre Order Payment riadky "
    "(zdroj variabilného symbolu a adresy zákazníka)."
)

col_u1, col_u2 = st.columns(2)
with col_u1:
    payments_file = st.file_uploader("1) Platby z Amazonu (xlsx)", type=["xlsx"])
with col_u2:
    invoices_file = st.file_uploader(
        "2) Vydané faktúry z Pohody (xml) - povinné pre Order Payment",
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
        df_orders, df_service_fees, df_refunds, skipped = load_orders(payments_file)
    except ProcessingError as e:
        st.error(str(e))
        st.stop()

    if skipped:
        st.warning(
            f"⚠️ {skipped} riadok(ov) bolo preskočených kvôli chýbajúcim hodnotám."
        )

    # Sumár načítaných riadkov
    summary_parts = []
    if not df_orders.empty:
        summary_parts.append(f"**{len(df_orders)}** Order Payment")
    if not df_service_fees.empty:
        summary_parts.append(f"**{len(df_service_fees)}** Service Fees")
    if not df_refunds.empty:
        summary_parts.append(f"**{len(df_refunds)}** Refund")

    if summary_parts:
        st.success("Načítané riadky: " + " | ".join(summary_parts))
    else:
        st.warning("V súbore sa nenašli žiadne spracovateľné riadky.")
        st.stop()

    invoice_lookup = {}
    if invoices_file is not None:
        try:
            invoice_lookup = parse_invoice_lookup(invoices_file)
            st.info(f"📇 Z faktúr načítaných {len(invoice_lookup)} záznamov (podľa čísla faktúry).")
        except Exception as e:
            st.error(f"Nepodarilo sa spracovať súbor s faktúrami: {e}")
            st.stop()
    elif not df_orders.empty:
        st.warning("⚠️ Nahraj XML export Vydaných faktúr z Pohody — povinné pre Order Payment riadky.")

    button_disabled = not df_orders.empty and invoices_file is None
    if st.button("🚀 Vygenerovať XML pre Pohodu", type="primary", disabled=button_disabled):
        try:
            result = generate_xml(
                df_orders, df_service_fees, df_refunds,
                int(statement_number), int(start_movement),
                invoice_lookup,
            )
        except ProcessingError as e:
            st.error(str(e))
            st.stop()

        # Sumár výsledku
        mv_start = int(start_movement)
        mv_end   = mv_start + result.movement_count - 1
        st.success(
            f"Hotovo! Vygenerovaných **{result.movement_count}** bankových pohybov:\n\n"
            f"- Order Payment: {result.order_count} objednávok × 3 = {result.order_count * 3} pohybov\n"
            f"- Service Fees: {result.service_fee_count} × 1 = {result.service_fee_count} pohybov\n"
            f"- Refund: {result.refund_count} × 3 = {result.refund_count * 3} pohybov\n\n"
            f"- Dátumový rozsah: {result.date_min} – {result.date_max}\n"
            f"- Číslo výpisu: {int(statement_number):03d}, pohyby: "
            f"{mv_start:04d} – {mv_end:04d}\n"
            f"- ID dataPacku: `{result.pack_id}`\n"
            f"- Adresa zákazníka napárovaná: {result.partner_matched} / "
            f"{result.order_count + result.refund_count}"
        )

        if result.unmatched_fakturas:
            with st.expander(
                f"❌ {len(result.unmatched_fakturas)} Order Payment objednávok BEZ faktúry v XML "
                "— pohyby sa nevytvorili"
            ):
                st.write(", ".join(result.unmatched_fakturas))

        if result.missing_partner_fakturas:
            with st.expander(
                f"⚠️ {len(result.missing_partner_fakturas)} faktúr bez adresy zákazníka "
                "(symVar sa použil, partnerIdentity sa vynechalo)"
            ):
                st.write(", ".join(result.missing_partner_fakturas))

        st.download_button(
            label=f"⬇️ Stiahnuť {result.filename}",
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
**Order Payment** (3 pohyby):
1. **Príjem** — suma = `Total product charges` (USD), predkontácia `úh.VFA USD`, obsahuje `symVar` + `symPar` + adresu zákazníka.
2. **Výdaj** — suma = `abs(Amazon fees)`, predkontácia `Uhr.OZ-USD`, text "POPLATOK ZA TRANSAKCIU".
3. **Výdaj** — suma = `Total (USD)`, predkontácia `Amaz.OZ-USD`, text "NETTO čIASTKKA FKSTURY".

**Service Fees** (1 pohyb):
- **Výdaj** — suma = `abs(Total (USD))`, predkontácia `Uhr.OZ-USD`, text "Service Fees".
- Dátum: stĺpec `Date` z Amazon reportu (stĺpec `Datum` zvyčajne chýba).
- Kurz: ak chýba, nastaví sa `rate=0` — Pohoda doplní automaticky z kurzového lístka NBS/ECB podľa dátumu.

**Refund** (3 pohyby):
- Rovnaká štruktúra ako Order Payment, hodnoty **záporné** (as-is z Amazon reportu).
- `Total product charges` je záporný, `Amazon fees` kladný (poplatok za refund), `Total (USD)` záporný.
- Pohyb 1: text "Refund FV č. {číslo faktúry}", bez symVar ak dobropis nie je v XML faktúr.

**Variabilný symbol** (`symVar`, `symPar`): berie sa z XML exportu Vydaných faktúr podľa čísla faktúry.

**Názov súboru**: `Banka_Eppo_USD_{výpis}_{od}-{do}_{dátum_od}-{dátum_do}.xml`
        """
    )
