# End-to-End Clinical Data Pipeline (CDISC-orientiert)

## Überblick
Dieses Projekt implementiert eine **reproduzierbare, qualitätsgesicherte End-to-End-Datenpipeline**
für klinische Forschungsdaten. Der Fokus liegt auf klaren Datenlayern, expliziten
Data-Quality-Gates, **CDISC-orientierten** Outputs (SDTM / ADaM) sowie Governance-, Audit- und
Archivierungsaspekten.

Die Pipeline verarbeitet synthetische klinische Daten (Synthea) von **Rohdaten**
bis hin zu **Analyse, Machine Learning und revisionssicherer Archivierung**.

---

## Schnellstart (wichtig)
Damit die Pipeline überhaupt startet, müssen **zuerst** die Python-Abhängigkeiten installiert werden.
Das Repository enthält **kein Docker** und keine vorgefertigte Environment-Datei – du setzt die Umgebung lokal auf.

### Voraussetzungen
- Python **≥ 3.10**
- Internetzugang (nur für Step 01: Download)

### Empfohlen: virtuelle Umgebung (venv)
Windows:

    python -m venv .venv
    .venv\Scripts\activate

macOS / Linux:

    python3 -m venv .venv
    source .venv/bin/activate

### Dependencies installieren
Im Projekt-Root:

    pip install --upgrade pip
    pip install -r requirements.txt

### Pipeline ausführen (One-Click Runner)
Der Runner ruft alle Steps 01–13 nacheinander als Module auf:

    python -m scripts.00_run_pipeline

Optional: ab einem bestimmten Step starten (z.B. ab Step 6):

    python -m scripts.00_run_pipeline --from 6

### Einzelne Steps ausführen (optional)
Beispiele:

    python -m scripts.01_download_extract
    python -m scripts.04_staging_qc
    python -m scripts.13_archive

---

## Zielsetzung
- Aufbau einer strukturierten Datenpipeline  
  *(Raw → Staging → Curated → SDTM → ADaM → Marts → ML → Archiv)*
- Sicherstellung von Datenqualität durch explizite QC-Gates
- CDISC-orientierte Aufbereitung für regulatorische und analytische Nutzung
- Reproduzierbarkeit durch Manifeste, deterministische Schritte und Versionierung
- Nachvollziehbarkeit durch Dokumentation und Reports
- Demonstration einer vollständigen Data-Science- / ML-Kette

---

## Architektur 
Die Pipeline wird in genau dieser Reihenfolge ausgeführt:

01. Download & Extract  
02. Raw Profiling  
03. Load Staging (DuckDB)  
04. Staging QC (DQ Gates)  
05. Curated Transformations (+ Reject/Quarantine)  
06. SDTM Mapping  
07. SDTM Mapping QC  
08. ADaM Mapping  
09. ADaM QC  
10. Marts Build  
11. ML Training  
12. Final Summary / Report  
13. Archivierung (SIP → AIP → DIP + Fixity)

---

## Projektstruktur (wie in `scripts/config.py` definiert)
Die Pfade/Ordner werden zentral in `scripts/config.py` definiert und durch `ensure_dirs()` automatisch erzeugt.

    project_root/
    │
    ├─ data/
    │   ├─ raw/            # Rohdaten (ZIP/CSV) – unverändert
    │   ├─ extracted/      # entpackte CSVs
    │   └─ duckdb/         # DuckDB Warehouse (warehouse.duckdb)
    │
    ├─ scripts/            # Pipeline-Skripte (00–13, nummeriert)
    │
    ├─ documentation/      # Fachliche Dokumentation je Schritt (Markdown)
    │
    ├─ out/                # Alle generierten Outputs und Artefakte
    │   ├─ manifests/
    │   ├─ raw_profiling/
    │   ├─ staging_qc/
    │   ├─ curated/
    │   ├─ sdtm/
    │   ├─ sdtm_qc/
    │   ├─ adam/
    │   ├─ adam_qc/
    │   ├─ marts/
    │   ├─ ml/
    │   └─ final/
    │
    ├─ archive/            # Archivpaket (Step 13 schreibt hierhin)
    │
    ├─ README.md
    └─ requirements.txt

---

## Pipeline-Steps (Kurzüberblick, passend zu den Modulnamen)

| Step | Modul | Inhalt |
|-----:|------|--------|
| 00 | scripts.00_run_pipeline | One-click Runner (führt 01–13 als Module aus) |
| 01 | scripts.01_download_extract | Download & sichere Entpackung der Rohdaten + Fixity/Manifest |
| 02 | scripts.02_raw_profiling | Deskriptives Profiling der Rohdaten (Missingness, Duplikate, Date-Parse) |
| 03 | scripts.03_load_staging | Laden in DuckDB (Staging `stg_*` + technische Spalten + Meta-Logs) |
| 04 | scripts.04_staging_qc | Technische & logische DQ-Checks + Speicherung in `meta_dq_results` |
| 05 | scripts.05_curated_transform | Curated Layer (Typisierung, Plausibilität, RI) + Reject Tabellen + Report |
| 06 | scripts.06_sdtm_mapping | Ableitung der SDTM-Domains (DM, SV, LB, VS, MH, CM, PR) |
| 07 | scripts.07_sdtm_mapping_qc | QC der SDTM-Daten (Coverage, RI, Timing, Duplicates, Unmapped) |
| 08 | scripts.08_adam_mapping | ADaM Erstellung (ADSL + ADBDS_LOS / LOS-Logik) |
| 09 | scripts.09_adam_mapping_qc | QC der ADaM-Datasets (Coverage, RI, Plausibility, Duplicates) |
| 10 | scripts.10_marts | Feature-Marts inkl. `mart_merged_for_lm` + Feature Coverage Policy |
| 11 | scripts.11_ml_train | Modelltraining & Evaluation (CV/Holdout, Metriken, Plots, Model Artefakt) |
| 12 | scripts.12_final_summary | Zusammenfassender Ergebnisbericht (HTML/JSON) |
| 13 | scripts.13_archive | Archivierung & Pseudonymisierung (SIP→AIP→DIP, Fixity SHA256) |

Detaillierte Beschreibungen befinden sich im Ordner **`documentation/`**.

---

## Technischer Stack (aus dem Code abgeleitet)
- **Programmiersprache:** Python  
- **Datenbank:** DuckDB (lokal, serverlos; `data/duckdb/warehouse.duckdb`)  
- **Formate:** CSV, JSON, PNG (Charts), optional Parquet/Arrow je Step  
- **Visualisierung:** Matplotlib  
- **Machine Learning:** scikit-learn + joblib  
- **Governance:** Manifeste, QC-Reports, Checksums (SHA256)  
- **Archivierung:** OAIS-orientiert (SIP/AIP/DIP), Fixity, Inventar

---

## Data Governance & Qualität
- Jeder Pipeline-Schritt erzeugt ein **Manifest** (JSON) in `out/manifests/`
- QC-Ergebnisse werden als **CSV- und HTML-Reports** persistiert
- Rejects werden nicht gelöscht, sondern explizit dokumentiert (Quarantine-Prinzip)
- Keine impliziten Transformationen oder „stille“ Imputationen
- Klare Trennung von Layern über Präfixe: `stg_`, `cur_`, `rej_`, `sdtm_`, `adam_`, `mart_`

---

## Archivierung & FAIR
Der letzte Pipeline-Schritt (`scripts.13_archive`) erzeugt ein revisionssicheres Archivpaket:
- **SIP → AIP → DIP**
- Checksums (Fixity, SHA256)
- Inventarliste + Fixity-Verification JSON
- Pseudonymisierung (USUBJID → SUBJ_HASH) und heuristische Identifier-Drops

Orientiert an:
- **OAIS (ISO 14721)**
- **FAIR-Prinzipien** (Findable, Accessible, Interoperable, Reusable)

---

## Hinweis
Die verwendeten Daten (Synthea) sind **synthetisch** und enthalten **keine realen Patientendaten**.
