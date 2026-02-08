# STEP 05 – Curated Transformations

## Zweck
Erzeugung eines fachlich bereinigten Curated Layers
inklusive Reject-Tabellen.

## Input
- Staging-Tabellen aus DuckDB
- Manifest aus Step 03/04

## Output
- Curated-Tabellen (`cur_*`)
- Reject-Tabellen (`rej_*`)
- HTML-DQ-Report
- Manifest: `manifest_curated.json`

## Zentrale Regeln
- Pflichtschlüssel müssen vorhanden sein
- Zeitliche Plausibilität wird erzwungen
- Negative Kosten werden ausgeschlossen
- Rejects werden nicht gelöscht, sondern dokumentiert

## Bedeutung
Curated ist die **erste fachlich vertrauenswürdige Ebene**
der Pipeline.
