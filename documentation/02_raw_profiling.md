# STEP 02 – Raw Profiling

## Zweck
Exploratives, nicht-invasives Profiling der Rohdaten zur frühen
Erkennung struktureller Probleme.

## Input
- CSV-Dateien aus dem Raw-Layer
- Manifest: `manifest_raw.json`

## Output
- CSV-Reports (Missing Rates, Duplikate, Datumsfelder)
- Manifest: `manifest_raw_profiled.json`

## Zentrale Logik
- Erkennung potenzieller Datumsfelder
- Parsing-Qualität von Datumswerten
- Duplikat-Zählung auf Zeilenebene
- Top-Missing-Spalten

## Data-Quality / Annahmen
- Keine Filterung oder Korrektur
- Ergebnisse sind rein deskriptiv

## Bedeutung
Frühe Transparenz über Datenqualität ohne
Einfluss auf nachgelagerte Verarbeitung.
