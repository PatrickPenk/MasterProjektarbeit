# STEP 04 – Staging Quality Checks

## Zweck
Überprüfung der technischen und inhaltlichen Mindestqualität
der Staging-Daten.

## Input
- DuckDB Staging-Tabellen
- Manifest aus Step 03

## Output
- QC-Ergebnisse in `meta_dq_results`
- HTML- & CSV-Reports
- Manifest: `manifest_staging_checked.json`

## Geprüfte Aspekte
- Tabellenexistenz (HARD)
- Technische Pflichtspalten (HARD)
- Run-ID-Konsistenz (HARD)
- Referentielle Integrität (SOFT)
- Plausibilität (z. B. Zeitlogik, Kosten ≥ 0)
- Duplikat-Heuristiken

## Bedeutung
Erstes formales Quality Gate der Pipeline.
Fehler werden transparent dokumentiert.
