# STEP 01 – Download & Extract (Raw)

## Zweck
Beschaffung der Rohdaten (Synthea CSV Export) und sichere Entpackung
in eine kontrollierte Raw-Zone.

## Input
- Öffentliche Synthea ZIP-Datei (CSV Export)

## Output
- ZIP-Datei unter `data/raw/`
- Entpackte CSVs unter `data/extracted/`
- SHA256-Checksum der ZIP-Datei
- Manifest: `manifest_raw.json`

## Zentrale Logik
- Download mit Integritätsprüfung
- Schutz vor ZipSlip (unsichere Pfade)
- Automatische Erkennung des CSV-Root-Verzeichnisses

## Data-Quality / Annahmen
- Rohdaten werden **nicht verändert**
- Integrität wird über Checksums abgesichert

## Bedeutung
Dieser Schritt definiert die unveränderte Datenquelle
als Ausgangspunkt für alle weiteren Transformationen.
