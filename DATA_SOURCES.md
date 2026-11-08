
```markdown
# DATA_SOURCES

Unofficial extracts for research. Verify against the source before use. Each table carries its own pull date
(`pulled_on`, `source_last_updated`) and git history shows snapshot dates.

## 1. Agmarknet daily mandi prices (table mandi_prices)
- Publisher: Directorate of Marketing & Inspection (DMI), Ministry of Agriculture & Farmers Welfare, via Open Government Data Platform India (data.gov.in)
- Resource id: 9ef84268-d588-465a-a308-a864a43d0070 ; endpoint api.data.gov.in/resource/<id>
- Licence: Government Open Data License - India (GODL-India); acknowledge source prominently; do not mislead; no implied endorsement.
- Fields: state, district, market, commodity, variety, grade, arrival_date (dd/mm/yyyy), min_price, max_price, modal_price (Rs per quintal), pulled_on.
- Coverage: current-day feed only (~16,000 rows/day). Held: 2026-07-23..2026-09-24 on 39 pull days (+ a 4-row stub for 2026-07-21).
- Refresh: collection stopped after 2026-09-24; days before 2026-07-23 cannot be back-filled.
- Caveats: dd/mm/yyyy strings; 8.5k duplicate rows; 23 rows min>max; placeholder prices (<= Rs 1) in 259 rows; prices for flowers/per-piece items are not per quintal.
- Collection method: paginated API calls with data.gov.in's public sample key (10 records per page); register a personal key (DATA_GOV_IN_KEY) for production use.

## 2. World Bank agriculture indicators (worldbank_agri)
- Publisher: The World Bank, World Development Indicators via api.worldbank.org/v2 ; Licence: CC BY 4.0 ("The World Bank, WDI, retrieved <date>").
- Nine indicators x IN CN BR US ID VN TH, 1960-2025.

## 3. FAOSTAT (faostat_producer_prices, faostat_crops)
- Publisher: Food and Agriculture Organization of the UN, https://bulks-faostat.fao.org/production/ (Prices_E_All_Data, Production_Crops_Livestock)
- Licence: CC BY 4.0 + FAO database terms of use (https://www.fao.org/contact-us/terms/db-terms-of-use/en/)
- Producer prices LCU/SLC/USD per tonne, 1991-2025 (91,334 rows); area, production, yield 1961-2024 (140,295 rows). Flag E = estimated, I = imputed (30% and 12% of crop rows).
- Collection: bulk CSV download, no key.

## 4. NASA POWER (nasa_power_monthly)
- Publisher: NASA Langley Research Center POWER Project, https://power.larc.nasa.gov/ ; open data, attribution requested.
- Monthly rainfall (mm/day) and 2 m mean temperature for one representative point in each of 14 Indian agricultural states, 2015-2025.
- Caveat: single point per state is not a state average.

## 5. FCI depot directory (fci_locations)
- Publisher: Food Corporation of India, https://fci.gov.in/depot-details ; licence not stated (confirm with FCI).
- 478 depots in 5 zones / 25 regions. Collected through the site's own API, which wraps requests in AES-128-CBC/HMAC with client-side constants from the public JS bundle. Do not republish raw_json or the IP-based depot links.

## 6. docs/gobardhan
- Seven Gazette of India notifications (Ministry of Agriculture & Farmers Welfare, DA&FW, 14 Nov 2023 - 25 Feb 2026) authorising bulk sale of Fermented Organic Manure under clause 22(c) of the FCO 1985; downloaded 2026-07-27 from https://gobardhan.eil.co.in/important-circular. Text only, no PDFs committed.
- HS 3101 trade tables from TradeStat (DGCI&S).
- Staff contact tables are removed from the public repository.
```
