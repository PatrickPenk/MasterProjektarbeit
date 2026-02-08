# STEP 03 – Load Staging

## Zweck
Laden der Rohdaten in eine relationale Datenbank (DuckDB)
inklusive technischer Metadaten.

## Input
- CSV-Dateien aus Raw
- Manifest aus Step 01 oder 02

## Output
- Staging-Tabellen (`stg_*`) in DuckDB
- Metatabellen (`meta_runs`, `meta_stg_load`)
- Manifest: `manifest_staging.json`

## Zentrale Logik
- Jede Tabelle erhält:
  - `_run_id`
  - `_ingested_at`
  - `_source_file`
- Row Counts werden protokolliert
- Integrität über File-Hashes

## Data-Quality / Annahmen
- Keine inhaltliche Validierung
- Staging entspricht technisch dem Input

## Bedeutung
Staging ist die technische Eintrittsschicht
für alle nachfolgenden Qualitätsprüfungen.
