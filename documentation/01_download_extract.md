# Step 01 – Download & Extract (Raw Layer)
**Layer:** External Source → Raw  
**Ziel:** Reproduzierbarer, integrer Import des Synthea-Datensatzes als Ausgangspunkt der Pipeline.

---

## 1. Zweck und Rolle im End-to-End-Prozess

Dieser Schritt stellt sicher, dass die Rohdaten:
- **deterministisch** verfügbar sind (gleiches Inputpaket),
- **integritätsgesichert** sind (Fixity per SHA256),
- **sicher entpackt** werden (ZipSlip-Schutz),
- und als **klarer CSV-Root** für Folgeprozesse bereitliegen.

Damit ist Step 01 die Grundlage für:
- konsistente `run_id`-basierte Nachvollziehbarkeit,
- spätere DQ-Checks (Staging/Curated/SDTM/ADaM),
- und Archivierungsanforderungen (Fixity/Provenienz).

---

## 2. Kernlogik (konzeptionell)

### 2.1 Download (Atomic & robust)
- Lädt ein ZIP-Archiv von einer definierten Quelle herunter (HTTP Streaming).
- Schreibt zuerst eine temporäre `.part` Datei und ersetzt anschließend atomar die Zieldatei.
- Verhindert “halb” heruntergeladene Artefakte im Raw Layer.

### 2.2 Fixity / Integritätsprüfung
- Berechnet SHA256 über die ZIP-Datei.
- Speichert die Prüfsumme als Artefakt (`zip_sha256.txt`).
- Zweck: Nachweis, dass Inputdaten unverändert sind (Audit/Archivierung).

### 2.3 Sicheres Entpacken (ZipSlip)
- Validiert jeden ZIP-Pfad (kein Ausbruch aus dem Zielverzeichnis möglich).
- Erst dann Entpacken nach `data/extracted/`.
- Marker-Datei signalisiert: Entpacken war vollständig und wiederholbar.

### 2.4 CSV Root Detection
- Ermittelt automatisch den Ordner, in dem die meisten CSV-Dateien liegen.
- Zweck: robust gegen wechselnde ZIP-Strukturen.

### 2.5 “Core Tables” Präsenzcheck
- Prüft die Existenz definierter Kern-Tabellen (Contract).
- Ergebnis wird im Manifest festgehalten (ok/missing).

---

## 3. Data Contract (Core Tables)

Erwartete Kern-Tabellen (mindestens):
- patients
- encounters
- conditions
- procedures
- medications
- observations

Die Pipeline kann grundsätzlich auch mit Teilmengen laufen, aber:
- viele nachgelagerte Checks/Mapping-Schritte basieren auf diesen Entitäten.

---

## 4. Outputs / Artefakte

### Dateien
- ZIP in `data/raw/`
- entpackte CSVs in `data/extracted/`
- `out/manifests/manifest_raw.json` (Input-Manifest)
- `out/manifests/zip_sha256.txt` (Fixity)

### Typische Manifest-Inhalte
- Pfad zum CSV Root
- Mapping: Tabellenname → CSV Pfad
- Liste missing/found Core Tables
- Status-Flags (ok)

---

## 5. Qualitäts- & Governance-Aspekte

- **Reproduzierbarkeit:** deterministische Quelle + Fixity
- **Sicherheit:** ZipSlip-Schutz verhindert Directory Traversal
- **Audit Trail:** Hash + Manifest dokumentieren Provenienz des Inputs

---

## 6. Fehlerfälle (typisch) und Bedeutung
- Keine CSVs gefunden → Input beschädigt / falsche ZIP-Struktur
- Hash nicht speicherbar → Rechte/Filesystem
- Entpacken unsicher → potenzielles Sicherheitsrisiko, daher Hard-Fail
