# Projektüberblick – End-to-End Datenpipeline (CDISC-orientiert)

## Ziel
Ziel dieses Projekts ist der Aufbau einer reproduzierbaren, qualitätsgesicherten
End-to-End-Datenpipeline für klinische Forschungsdaten – von Rohdaten bis zu
Analyse, Modellierung und Archivierung.

Der Fokus liegt auf:
- klaren Layern (Raw → Staging → Curated → SDTM → ADaM → Marts)
- expliziten Data-Quality-Gates
- CDISC-konformer Strukturierung
- Governance, Reproduzierbarkeit und Audit-Tauglichkeit

## Datenfluss (High-Level)
1. Rohdaten-Download (Synthea)
2. Technisches Profiling
3. Laden in Staging (DuckDB)
4. Data-Quality-Prüfung (Staging)
5. Curated Layer mit Rejects
6. SDTM-Mapping
7. SDTM Quality Checks
8. ADaM-Mapping
9. ADaM Quality Checks
10. Analytische Marts
11. ML-Modelltraining
12. Finaler Ergebnisbericht
13. Archivierung (OAIS-orientiert)

## Technische Umsetzung
- Datenbank: DuckDB (lokal, reproduzierbar)
- Verarbeitung: Python-Skripte (nummeriert, deterministisch)
- Outputs: CSV, HTML-Reports, JSON-Manifeste
- Archivierung: Checksums, Inventar, anonymisierte Exporte

## Governance-Prinzipien
- Jede Phase erzeugt ein Manifest
- QC-Ergebnisse sind explizit dokumentiert
- Keine impliziten Transformationen
- Trennung von Daten, Logik und Dokumentation
