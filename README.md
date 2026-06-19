# Amazon platby -> Pohoda banka (XML import)

Streamlit aplikácia, ktorá z xlsx exportu Amazon platieb (stĺpce `Transaction type`,
`Total product charges`, `Amazon fees`, `Total (USD)`, `Kurz`, `Datum`, `Faktura`, `VS`)
vygeneruje XML súbor s bankovými pohybmi pripravený na import do Pohody (formát
`bank.xsd` v2.0, podľa vzoru `Banka_Eppo_USD_1.xml`).

Voliteľne (ale odporúčane) môžeš nahrať aj XML export **Vydaných faktúr** z Pohody
(`inv:invoice` formát) - z neho sa podľa `inv:symVar` napáruje `bnk:partnerIdentity`
(adresa zákazníka) pre prvý z 3 pohybov každej objednávky.

## Spustenie lokálne

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Nasadenie na Streamlit Community Cloud

1. Nahraj tento priečinok (`app.py`, `pohoda_xml.py`, `requirements.txt`) do GitHub repozitára.
2. Na [share.streamlit.io](https://share.streamlit.io) vytvor novú appku, vyber repo a `app.py` ako vstupný súbor.
3. Po nasadení nahraj xlsx priamo v appke.

## Súbory

- `app.py` - Streamlit UI (upload, vstup čísla výpisu a začiatočného čísla pohybu, stiahnutie XML).
- `pohoda_xml.py` - logika spracovania (čítanie xlsx, generovanie XML, konštanty firmy/účtovania).

## Úprava firemných/účtovných konštánt

Na začiatku `pohoda_xml.py` v sekcii `KONŠTANTY` sú nastavené:
- IČO, číslo účtu (`USDA`), predkontácie (`úh.VFA USD`, `Uhr.OZ-USD`, `Amaz.OZ-USD`),
- texty pohybov, adresa vlastnej firmy (EPPO BRANDS s. r. o.).

Ak sa tieto údaje zmenia, uprav ich priamo tam.
