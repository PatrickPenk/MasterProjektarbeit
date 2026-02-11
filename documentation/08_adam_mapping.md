# Step 08 – ADaM Mapping (SDTM → ADaM: ADSL + ADBDS_LOS)
**Layer:** SDTM → ADaM  
**Standard:** CDISC ADaM (vereinfachtes, projektorientiertes Subset)  
**Ziel:** Analyse-Datensätze erzeugen: Subject-Level (ADSL) + BDS (LOS)

---

## 1. Zweck und Rolle

ADaM ist der Analyse-Standard, der aus SDTM:
- “analysis-ready” Variablen ableitet,
- klare Analysepopulationen/Strukturen erzeugt,
- und Variablen-Definitionen/Flags nachvollziehbar macht.

In diesem Projekt:
- ADSL: 1 Zeile pro Subjekt (Demografie + Index LOS)
- ADBDS_LOS: BDS-Struktur für LOS pro Visit/Index

---

## 2. Preconditions

Erwartete SDTM Tabellen:
- sdtm_dm (Demografie, Subjekt-Basis)
- sdtm_sv (Zeitanker / Visits)

Wenn diese fehlen → Step 06/07 unvollständig.

---

## 3. LOS Berechnungslogik (zentral)

### 3.1 Problem
In realen Daten fehlen häufig Endzeiten (SVENDTC). Für LOS braucht man aber Start+End.

### 3.2 Lösung: Imputation mit “Next Visit Start”
Der Step erzeugt eine Sequenz pro USUBJID:
- sortiert nach SVSTDTC (+ VISITNUM)
- berechnet NEXT_SVSTDTC als Lead()

Dann:
- wenn SVENDTC vorhanden → nutze SVENDTC
- wenn SVENDTC fehlt, aber NEXT_SVSTDTC vorhanden → imputiere End = NEXT_SVSTDTC
- wenn beides fehlt → End bleibt unresolved

### 3.3 Flags (Auditierbarkeit)
- END_IMPUTEDFL = 1, wenn End imputiert
- END_MISSING_UNRESOLVEDFL = 1, wenn End nicht bestimmbar

### 3.4 AVAL/LOS
LOS wird als Zeitdifferenz berechnet:
- mindestens 0 (GREATEST(0.0, diff))

---

## 4. ADBDS_LOS (BDS Dataset)
Enthält i.d.R. pro Visit:
- USUBJID
- VISIT/VISITNUM
- AVAL (LOS)
- Flags zu End-Imputation
- Originale DTC Felder (Start/End) + imputiertes End

Ziel:
- “long format” Analysebasis.

---

## 5. ADSL (Subject-Level)
Konsolidiert:
- Demografie aus DM (SEX, BRTHDTC → AGE Ableitung falls vorhanden)
- Index-Visit basierte LOS Kennzahlen:
  - INDEX_LOS_DAYS
  - INDEX_END_IMPUTEDFL
  - INDEX_END_MISSING_UNRESOLVEDFL

Ziel:
- “wide format” pro Subjekt.

---

## 6. Reporting & Outputs

### Dateien
- adsl.csv
- adbds_los.csv
- HTML Report (Mapping Summary)
- JSON Results
- manifest_adam.json

### DuckDB Tabellen
- adam_adsl
- adam_adbds_los

---

## 7. Warum Step 08 kritisch ist
- ADaM ist der direkte Input für QC (Step 09), Marts (Step 10) und ML (Step 11).
- Ohne nachvollziehbare Imputation/Flags wären LOS-Analysen intransparent.
- BDS-Struktur ermöglicht Validierung (Duplikate, AVAL Coverage, Plausibility).
