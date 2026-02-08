# STEP 08 – ADaM Mapping

## Zweck
Erstellung analysereifer ADaM-Datasets
(ADSL, ADBDS_LOS).

## Input
- SDTM DM und SV
- Manifest aus Step 06/07

## Output
- ADaM-Tabellen
- CSV-Exporte
- HTML-Report
- Manifest: `manifest_adam.json`

## Zentrale Regeln
- Index-Visit = erster Visit
- LOS-Imputation nur, wenn fachlich begründbar
- 1 Zeile pro Subject in ADSL

## Bedeutung
ADaM ist die Analyseebene für statistische
Auswertungen und ML.
