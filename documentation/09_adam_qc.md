# Step 09 – ADaM QC (ADSL + ADBDS_LOS)
**Layer:** ADaM  
**Ziel:** Sicherstellen, dass die ADaM Analyse-Datensätze vollständig, konsistent und plausibel sind, bevor sie für Feature Engineering / ML genutzt werden.

---

## 1. Zweck und QC-Philosophie

ADaM QC dient als “analysis readiness gate”.
Er prüft:
- ob Schlüsselvariablen vorhanden sind (Coverage/Completeness),
- ob Subjektbeziehungen stimmen (RI),
- ob Analysewerte plausibel sind (Plausibility),
- und ob Duplikate/Strukturschäden existieren (Duplicates).

Der Step produziert maschinenlesbare Artefakte (CSV/JSON) und ein HTML-Report-File,
damit QC nachvollziehbar in die Dokumentation eingebunden werden kann.

---

## 2. Preconditions

Erwartete Tabellen:
- adam_adsl
- adam_adbds_los
- sdtm_dm (als Referenz für RI)

Fehlende Tabellen → Hard-Fail, da QC sonst nicht sinnvoll.

---

## 3. QC-Kategorien im Detail

### 3.1 Coverage / Completeness
Misst pro Dataset Schlüsselspalten:
- Anteil nicht-missing (present_pct)
- n_missing / n_total

Beispiele:
**ADSL**
- INDEX_LOS_DAYS
- INDEX_END_IMPUTEDFL
- INDEX_END_MISSING_UNRESOLVEDFL
- AGE

**ADBDS_LOS**
- AVAL
- END_IMPUTEDFL
- END_MISSING_UNRESOLVEDFL

Ziel:
- Nachweis, dass ADaM “analysis-ready” Felder nicht leer sind.

Output:
- adam_qc_coverage.csv
- Coverage Chart

---

### 3.2 Referential Integrity (USUBJID ↔ DM)
Prüft:
- ADSL.USUBJID muss in DM.USUBJID existieren
- optional “Reconciliation”: DM Subjekte sollten in ADSL vorhanden sein (Abweichungen sichtbar machen)

Ziel:
- verhindert, dass Analysepopulation Subjekte “verliert” oder Orphans erzeugt.

Output:
- adam_qc_ri.csv
- RI Chart

---

### 3.3 Plausibility Checks (LOS)
Typische Prüfungen:
- Negative LOS (AVAL < 0 oder INDEX_LOS_DAYS < 0)
- extreme Werte (Outlier-Überblick; je nach Implementierung über Schwelle/Quantile)
- unresolved Imputation Fälle (END_MISSING_UNRESOLVEDFL = 1) als Risikoindikator

Ziel:
- Verhindert stillschweigende Zeitlogikfehler.

Output:
- adam_qc_plausibility.csv
- Plausibility Chart (LOS)

---

### 3.4 Duplicate Checks
ADSL:
- USUBJID muss einzigartig sein (1 Row per Subject)

ADBDS_LOS:
- Schlüsselbasierte Duplikate (z.B. USUBJID + VISIT/VISITNUM + Zeitanker)

Ziel:
- Verhindert Join-Explosionen in Marts und falsche Trainingslabels in ML.

Output:
- adam_qc_duplicates.csv

---

## 4. Gate-Verhalten (STRICT_QC)

- Default: report-only (Pipeline läuft weiter)
- Optional: STRICT_QC kann Hard-Fail auslösen, wenn “hard_failures” existieren.

---

## 5. Outputs / Artefakte

- CSV: coverage / RI / plausibility / duplicates
- PNG Charts: Coverage, RI, LOS Plausibility
- HTML Report
- JSON Summary
- manifest_adam_checked.json (QC-Manifest)

---

## 6. Bedeutung für Step 10/11
- Step 10 (Marts) setzt stabile ADSL/LOS-Variablen voraus.
- Step 11 (ML) ist extrem sensitiv gegenüber Duplikaten/Label-Fehlern.
ADaM QC ist daher das letzte Qualitäts-Gate vor Feature Engineering.
