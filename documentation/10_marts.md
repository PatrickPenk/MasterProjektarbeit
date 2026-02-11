# Step 10 – Marts Build (Analytics Layer)
**Layer:** ADaM/SDTM → Analytics (Marts)  
**Ziel:** Erstellung analysierbarer Datensätze (“data products”) inklusive Feature QC und Missingness-Policy.

---

## 1. Zweck und Rolle

Step 10 baut aus standardisierten klinischen Daten (SDTM/ADaM) konkrete Analyse-Marts:
- Wide-Tabellen (Features pro Subjekt/Visit)
- Aggregationen
- Lab Feature Tables
- finaler Merge für ML (`mart_merged_for_lm`)

Er ist der “Feature Engineering”-Step, der:
- Struktur vereinheitlicht,
- Feature Coverage messbar macht,
- und die Daten für Modelltraining vorbereitet.

---

## 2. Inputs (über Manifest)
Bezieht run_id aus:
- manifest_adam_checked.json (oder fallback SDTM manifests)

Erwartet in DuckDB typischerweise:
- ADaM: adam_adsl, adam_adbds_los
- SDTM: sdtm_sv, sdtm_lb, etc. (je nach Mart)

---

## 3. Missingness & Normalisierung

### 3.1 Missing Tokens Normalization
Behandelt typische “pseudo-missing” Strings:
- "", "nan", "none", "null", "na", "n/a", "nat"
→ werden zu NA konvertiert

Ziel:
- verhindert, dass “nan” als echter String im Modell landet.

### 3.2 Inf Handling
- `inf/-inf` in numerischen Features → NA
Ziel:
- verhindert Modell-/Imputer-Fehler.

### 3.3 ID-Sicherheit
- USUBJID wird als String normalisiert, leere Token → NA

---

## 4. Feature QC: Coverage-Policy (LAB)
Da Laborwerte oft spärlich sind, wird eine Coverage Policy angewandt:

Global Policy:
- MIN_LAB_NON_MISSING_RATE (z.B. 1%)
- MIN_LAB_NON_MISSING_COUNT (z.B. 20)

Regel:
- Feature wird gedroppt, wenn **rate < min_rate UND count < min_count**

Outputs:
- `lab_feature_coverage.csv`
- `lab_features_dropped.csv`

Ziel:
- verhindert “ultra-sparse” Features, die ML destabilisieren und kaum Information tragen.

---

## 5. Imputation-Strategie (LAB)
Für Features nach Aggregation:

- LAB_*_count: NA → 0 (int)
- LAB_*_MEAN:
  - wenn komplett NA → drop (keine beobachteten Werte)
  - sonst: NA → Median (Fallback 0.0)

Ziel:
- robuste Features ohne “no observed values” Probleme.

---

## 6. Marts (typisch)
Erzeugt mehrere CSV/Tabellen, u.a.:
- mart_wide_from_sv
- mart_wide_all_visits
- mart_wide_aggregation
- mart_lab_values
- mart_adsl_demo
- mart_merged_for_lm (finaler Trainingsdatensatz)

---

## 7. Outputs / Artefakte

- DuckDB Marts Tabellen
- CSV Exporte in out/marts/
- Feature QC Reports (Coverage/Dropped)
- manifest_marts.json (Artefakt-Referenzen + run_id)

---

## 8. Bedeutung für ML
Der Step entscheidet faktisch:
- welche Features ins Modell gehen,
- wie Missingness behandelt wird,
- und ob Daten stabil genug für Training sind.
