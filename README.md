# End-to-End Clinical Data Pipeline (CDISC-orientiert)

## Überblick

Dieses Projekt implementiert eine **reproduzierbare, qualitätsgesicherte
End-to-End-Datenpipeline** für klinische Forschungsdaten auf Basis
synthetischer Synthea-Daten.

Die Pipeline deckt den vollständigen Datenlebenszyklus ab:

Raw → Profiling → Staging → DQ-Gates → Curated → SDTM → SDTM QC → ADaM →
ADaM QC → Marts → ML → Final Report → Archivierung

Der Fokus liegt auf: - klar definierten Datenlayern - expliziten Quality
Gates - CDISC-orientierter Strukturierung (SDTM / ADaM) -
Reproduzierbarkeit durch Manifeste - Auditierbarkeit durch QC-Reports -
Governance & Archivierung (OAIS-orientiert)

------------------------------------------------------------------------

# 🚀 Pipeline starten (empfohlener Ablauf)

## 1) Voraussetzungen

-   Python ≥ 3.10
-   Kein Docker erforderlich
-   Keine Conda-/Poetry-Installation notwendig

------------------------------------------------------------------------

## 2) Virtuelle Umgebung anlegen (dringend empfohlen)

Windows:

    python -m venv .venv
    .venv\Scripts\activate

macOS / Linux:

    python3 -m venv .venv
    source .venv/bin/activate

------------------------------------------------------------------------

## 3) Abhängigkeiten installieren

Alle benötigten Bibliotheken sind in `requirements.txt` definiert:

    pip install --upgrade pip
    pip install -r requirements.txt

Ohne diesen Schritt kann die Pipeline nicht ausgeführt werden.

------------------------------------------------------------------------

## 4) Pipeline ausführen

Gesamte Pipeline (Steps 01--13):

    python -m scripts.00_run_pipeline

Optional: Ab einem bestimmten Schritt starten (z.B. Step 6):

    python -m scripts.00_run_pipeline --from 6

------------------------------------------------------------------------

# Architektur (Tatsächliche Reihenfolge laut Code)

01 Download & Extract\
02 Raw Profiling\
03 Load Staging (DuckDB)\
04 Staging QC (DQ-Gates)\
05 Curated Transform (+ Reject Layer)\
06 SDTM Mapping\
07 SDTM QC\
08 ADaM Mapping (ADSL + ADBDS_LOS)\
09 ADaM QC\
10 Marts Build (mart_merged_for_lm)\
11 ML Training & Evaluation\
12 Final Summary Report\
13 Archive Package

------------------------------------------------------------------------

# Projektstruktur

data/ raw/ Rohdaten (ZIP) extracted/ Entpackte CSV-Dateien duckdb/
warehouse.duckdb

scripts/ 00_run_pipeline.py 01--13 Pipeline Steps

out/ manifests/ raw_profiling/ staging_qc/ curated/ sdtm/ sdtm_qc/ adam/
adam_qc/ marts/ ml/ final/

archive/ Archivpaket (AIP/DIP, Fixity)

------------------------------------------------------------------------

# Data Governance & Qualität

-   Jeder Schritt erzeugt ein Manifest (JSON)
-   QC-Reports werden als CSV und HTML gespeichert
-   Reject-Datensätze werden nicht gelöscht
-   Keine stillen Transformationen
-   Klare Layer-Trennung (stg\_, cur\_, sdtm\_, adam\_, mart\_)

------------------------------------------------------------------------

# Archivierung (Step 13)

-   SIP → AIP → DIP Struktur
-   SHA256 Fixity
-   Pseudonymisierung (USUBJID → SUBJ_HASH)
-   strukturierte Inventarliste

Orientierung an OAIS- und FAIR-Prinzipien.

------------------------------------------------------------------------

# Hinweis

Die verwendeten Daten (Synthea) sind synthetisch und enthalten keine
realen Patientendaten.
