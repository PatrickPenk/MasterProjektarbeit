# STEP 06 – SDTM Mapping

## Zweck
Ableitung standardisierter CDISC-SDTM-Domains
aus dem Curated Layer.

## Input
- Curated-Tabellen
- Manifest: `manifest_curated.json`

## Output
- SDTM-Domains: DM, SV, LB, VS, MH, CM, PR
- CSV-Exporte
- Manifest: `manifest_sdtm.json`

## Zentrale Regeln
- USUBJID wird eindeutig generiert
- Keine Imputationen
- VS/LB-Trennung über definierte Codes

## Bedeutung
SDTM ist die regulatorische Tabulationsebene
und Grundlage für ADaM.
