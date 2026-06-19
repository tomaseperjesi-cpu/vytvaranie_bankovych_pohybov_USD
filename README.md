# Amazon platby -> Pohoda banka (XML import)

Streamlit aplikácia, ktorá z xlsx exportu Amazon platieb (stĺpce `Transaction type`,
`Total product charges`, `Amazon fees`, `Total (USD)`, `Kurz`, `Datum`, `Faktura`)
vygeneruje XML súbor s bankovými pohybmi pripravený na import do Pohody (formát
`bank.xsd` v2.0, podľa vzoru `Banka_Eppo_USD_1.xml`).

XML export **Vydaných faktúr** z Pohody (`inv:invoice` formát) je **povinný vstup** -
slúži ako autoritatívny zdroj skutočného variabilného symbolu (`inv:symVar`, použije sa
pre `bnk:symVar` aj `bnk:symPar`) a adresy zákazníka (`inv:partnerIdentity`), ktoré sa
napárujú podľa čísla faktúry (`typ:numberRequested` <-> stĺpec `Faktura` v xlsx).
Objednávky, ku ktorým sa v exporte faktúr nenájde zodpovedajúca faktúra, sa do výstupu
nezahrnú (appka ich vypíše v zozname).

## Spustenie lokálne

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Nasadenie na Streamlit Community Cloud

1. Nahraj tento priečinok (`app.py`, `pohoda_xml.py`, `requirements.txt`) do GitHub repozitára.
2. Na [share.streamlit.io](https://share.streamlit.io) vytvor novú appku, vyber repo a `app.py` ako vstupný súbor.
3. Po nasadení nahraj v appke oba súbory (xlsx platby + xml faktúry).

## Súbory

- `app.py` - Streamlit UI (upload oboch súborov, vstup čísla výpisu a začiatočného čísla pohybu, stiahnutie XML).
- `pohoda_xml.py` - logika spracovania (čítanie xlsx, parsovanie faktúr, generovanie XML, konštanty firmy/účtovania).

## Úprava firemných/účtovných konštánt

Na začiatku `pohoda_xml.py` v sekcii `KONŠTANTY` sú nastavené:
- IČO, číslo účtu (`USDA`), predkontácie (`úh.VFA USD`, `Uhr.OZ-USD`, `Amaz.OZ-USD`),
- texty pohybov, adresa vlastnej firmy (EPPO BRANDS s. r. o.).

Ak sa tieto údaje zmenia, uprav ich priamo tam.
