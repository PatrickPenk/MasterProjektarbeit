# Step 04 – Staging DQ Checks (Tech + RI + Plausibility + Duplicates)
**Layer:** Staging  
**Ziel:** Sicherstellen, dass die geladenen Tabellen technisch korrekt und strukturell plausibel sind, bevor fachliche Transformation (Curated) startet.

---

## 1. QC-Strategie: Gate-Charakter
Dieser Schritt ist ein Qualitäts-Gate mit Severity-Logik:
- **HARD**: führt (bei STRICT_DQ=True) zu Pipeline-Fail
- **SOFT**: wird dokumentiert, stoppt Pipeline nicht standardmäßig

Zentrale Motivation:
- Fehler früh abfangen, bevor sie sich in SDTM/ADaM fortpflanzen.

---

## 2. Logging & Audit: meta_dq_results
Der Schritt schreibt jede Prüfung in `meta_dq_results` mit:
- run_id, checked_at, layer
- check_name, table_name
- severity (HARD/SOFT)
- n_bad
- status (PASS/FAIL/SKIP/ERROR)
- message

Damit entsteht ein auditierbarer QC-Trail.

---

## 3. Prüfumfang: Welche Tabellen werden geprüft?
- alle geladenen `stg_%` Tabellen
- plus “Core Staging Tables” (stg_patients, stg_encounters, …)

So wird verhindert, dass “nur ein Teil” stillschweigend ungeprüft bleibt.

---

## 4. Check-Kategorien

### 4A) Technische Checks (HARD)

#### A1: table_exists
- Tabelle muss existieren, sonst Hard-Fail.

#### A2: required_tech_columns
- `_run_id`, `_ingested_at`, `_source_file` müssen existieren.
- Begründung: Traceability, Reproduzierbarkeit, Lineage.

#### A3: run_id_consistency
- `_run_id` muss überall gesetzt und gleich der manifest run_id sein.
- Begründung: verhindert Mischläufe / inkonsistente Datenstände.

---

### 4B) Referential Integrity Checks (SOFT)
Prüft typische FK-Beziehungen:
- encounters.Patient referenziert patients.Id
- Child-Tabellen referenzieren patients
- Child-Tabellen referenzieren encounters (wo anwendbar)

Begründung:
- Ohne RI-clean Inputs entsteht später “orphan data”, fehlerhafte SV/MH/CM/PR Zuordnung.

Ergebnis:
- Anzahl Orphans / Missing Keys wird dokumentiert.

---

### 4C) Plausibility Checks (SOFT)
Typische Staging-Plausibilität:
- negative Kosten (falls Kostenfelder existieren)
- zeitliche Logik: Stop < Start
- Pflichtfelder fehlen (Heuristiken)

Begründung:
- Staging soll zwar noch “roh” sein, aber grobe Unplausibilitäten müssen sichtbar werden.

---

### 4D) Duplicate Checks (SOFT/HARD je nach Schwere)
- erkennt Id-Duplikate (PK-Heuristik)
- meldet Anzahl problematischer Gruppen

Begründung:
- Duplikate können Mapping/Join Explosionen verursachen.

---

## 5. Output

- `meta_dq_results` (vollständige Check-Historie)
- `manifest_staging_checked.json` (QC Summary + Status)

---

## 6. Bedeutung für Curated
Curated nutzt diese Ergebnisse als:
- Entscheidungsgrundlage (“welche Reject Reasons sind wichtig?”)
- Vorfilter, damit Curated nicht mit technischen Defekten umgehen muss.
