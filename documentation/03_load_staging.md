# Step 03 – Load Staging (DuckDB Ingestion)
**Layer:** Raw → Staging  
**Ziel:** Technisch saubere und nachvollziehbare Datenübernahme (inkl. Run-Metadaten), ohne fachliche Korrekturen.

---

## 1. Zweck und Rolle

Dieser Schritt lädt CSV-Dateien in DuckDB und erzeugt für jede Quelle eine `stg_*` Tabelle.
Er ist der “Ingestion”-Step in der Data-Pipeline:

- Raw bleibt unverändert (Archiv-/Provenienzprinzip).
- Staging ist die **arbeitsfähige** Datenbank-Repräsentation.
- Fachregeln kommen erst in Curated.

---

## 2. Input Steuerung über Manifest

Der Step nutzt ein Input-Manifest (präferiert profiled/raw manifest), das enthält:
- `csv_root`
- `csv_paths`: Tabelle → Dateipfad
- `run_id` (falls fehlt: wird erzeugt)

Damit ist der Ingestion-Lauf deterministisch dokumentiert.

---

## 3. Technische Metadaten-Anreicherung (pro Tabelle)

Jede `stg_*` Tabelle erhält zusätzlich:
- `_run_id` (Traceability / Data Lineage)
- `_ingested_at` (Zeitstempel des Loads)
- `_source_file` (Dateiname/Quelle)

Zweck:
- spätere QC kann run_id Konsistenz prüfen,
- spätere Reports können Artefakte pro Lauf zuordnen.

---

## 4. Integritäts-/Audit-Tracking

### 4.1 SHA256 pro Source-CSV
- Berechnung Hash über jede CSV.
- Speicherung im Load-Log (meta table).
- Fixity für Ingest-Level-Nachweis.

### 4.2 Meta Tabellen

#### meta_runs
- run_id
- started_at
- input manifest
- db_path

#### meta_stg_load
pro Tabelle:
- run_id, table_name, stg_table
- source_file, source_sha256
- ingested_at
- row_count
- status/message (OK/SKIP/ERROR)

---

## 5. Verhalten bei fehlenden CSVs
Wenn eine erwartete Tabelle im Manifest fehlt:
- wird geloggt als SKIP (missing CSV)
- Pipeline kann trotzdem laufen (je nach nachgelagerten Preconditions)

---

## 6. Outputs

- DuckDB Tabellen: `stg_<table>`
- Meta Tables: `meta_runs`, `meta_stg_load`
- Output Manifest: `manifest_staging.json` (run_id + Quellen + Status)

---

## 7. Bedeutung für spätere Steps
- Step 04 prüft technische Konsistenz des Staging Layers.
- Step 05 baut Curated Entities aus den `stg_*` Tabellen.
- Step 13 kann Load-Metadaten als Provenienz verwenden.
