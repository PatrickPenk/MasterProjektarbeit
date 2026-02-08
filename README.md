# End-to-End Clinical Data Pipeline (CDISC-orientiert)

## Überblick
Dieses Projekt implementiert eine **reproduzierbare, qualitätsgesicherte End-to-End-Datenpipeline**
für klinische Forschungsdaten. Der Fokus liegt auf klaren Datenlayern, expliziten
Data-Quality-Gates, CDISC-Konformität (SDTM / ADaM) sowie Governance-, Audit- und
Archivierungsaspekten.

Die Pipeline verarbeitet synthetische klinische Daten (Synthea) von **Rohdaten**
bis hin zu **Analyse, Machine Learning und revisionssicherer Archivierung**.

---

## Zielsetzung
- Aufbau einer strukturierten Datenpipeline  
  *(Raw → Staging → Curated → SDTM → ADaM → Marts)*
- Sicherstellung von Datenqualität durch explizite QC-Gates
- CDISC-konforme Aufbereitung für regulatorische und analytische Nutzung
- Reproduzierbarkeit durch Manifeste, deterministische Schritte und Versionierung
- Nachvollziehbarkeit durch Dokumentation und Reports
- Demonstration einer vollständigen Data-Science- / ML-Kette

---

## Architektur (High-Level)
```
Raw Data (ZIP / CSV)
↓
Raw Profiling
↓
Staging (DuckDB)
↓
Staging QC (DQ Gates)
↓
Curated Layer (+ Rejects)
↓
SDTM Domains
↓
SDTM QC
↓
ADaM Datasets
↓
ADaM QC
↓
Analytical Marts
↓
ML Training & Evaluation
↓
Final Report
↓
Archivierung (OAIS / FAIR)
```

---

## Projektstruktur
```
project_root/
│
├─ data/
│ ├─ raw/ # Original ZIP-Dateien (unverändert)
│ ├─ extracted/ # Entpackte CSVs
│ └─ duckdb/ # DuckDB Warehouse (warehouse.duckdb)
│
├─ scripts/ # Pipeline-Skripte (01–13, nummeriert)
│
├─ documentation/ # Fachliche Dokumentation je Pipeline-Schritt (Markdown)
│
├─ out/ # Alle generierten Outputs und Artefakte
│ ├─ curated/
│ ├─ sdtm/
│ ├─ sdtm_qc/
│ ├─ adam/
│ ├─ adam_qc/
│ ├─ marts/
│ ├─ ml/
│ ├─ final/
│ └─ archive/
│
├─ README.md # Diese Datei
└─ requirements.txt

```

---

## Pipeline-Steps (Kurzüberblick)

| Step | Skript | Inhalt |
|-----:|--------|--------|
| 01 | download_extract | Download & sichere Entpackung der Rohdaten |
| 02 | raw_profiling | Deskriptives Profiling der Rohdaten |
| 03 | load_staging | Laden in DuckDB (Staging) |
| 04 | staging_qc | Technische & fachliche Qualitätschecks |
| 05 | curated_transform | Fachlich bereinigter Curated Layer |
| 06 | sdtm_mapping | Ableitung der SDTM-Domains |
| 07 | sdtm_mapping_qc | Qualitätsprüfung der SDTM-Daten |
| 08 | adam_mapping | Erstellung analysereifer ADaM-Datasets |
| 09 | adam_qc | Qualitätsprüfung der ADaM-Datasets |
| 10 | marts | Feature-Marts für Analyse & ML |
| 11 | ml_train | Modelltraining & Evaluation |
| 12 | final_report | Zusammenfassender Ergebnisbericht |
| 13 | archive | Archivierung & Anonymisierung (OAIS-orientiert) |

Detaillierte Beschreibungen befinden sich im Ordner **`documentation/`**.

---

## Technischer Stack
- **Programmiersprache:** Python  
- **Datenbank:** DuckDB (lokal, serverlos)  
- **Datenformate:** CSV, Parquet, JSON  
- **Visualisierung:** Matplotlib  
- **Machine Learning:** scikit-learn  
- **Governance:** Manifeste, QC-Reports, Checksums  
- **Archivierung:** OAIS-Prinzipien, Fixity (SHA256), Inventare  

---

## Pipeline ausführen (One-Click)

```bash
python run_pipeline.py
```

---

## Ausführung der Pipeline

### Voraussetzungen
- Python ≥ 3.10

Empfohlene Installation:
```bash
pip install -r requirements.txt
```

---

## Data Governance & Qualität
- Jeder Pipeline-Schritt erzeugt ein **Manifest**
- QC-Ergebnisse werden als **CSV- und HTML-Reports** persistiert
- Rejects werden nicht gelöscht, sondern explizit dokumentiert
- Keine impliziten Transformationen oder „stille“ Imputationen
- Klare Trennung von:
  - technischer Qualität (Schema, Schlüssel, Run-IDs)
  - fachlicher Qualität (Plausibilität, zeitliche Logik)

---

## Archivierung & FAIR
Der letzte Pipeline-Schritt erzeugt ein revisionssicheres Archivpaket:
- anonymisierte Exporte
- Checksums (Fixity)
- Inventarliste
- strukturierte Metadaten (JSON)
- optionale ZIP-Auslieferung

Orientiert an:
- **OAIS (ISO 14721)**
- **FAIR-Prinzipien** (Findable, Accessible, Interoperable, Reusable)

---

## Kontext & Nutzung
Dieses Projekt wurde im Rahmen einer **Projektarbeit im Umfeld von Datenmanagement,
Data Engineering und Data Governance** erstellt.  
Es dient der Demonstration bewährter Konzepte entlang des gesamten
Datenlebenszyklus klinischer Forschungsdaten.

---

## Hinweis
Die verwendeten Daten (Synthea) sind **synthetisch** und enthalten
**keine realen Patientendaten**.
