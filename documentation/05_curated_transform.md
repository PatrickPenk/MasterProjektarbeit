# Step 05 – Curated Transformations
**Layer:** Staging → Curated  
**Ziel:** Fachlich valide, typisierte und referenziell konsistente Entitäten als Grundlage für SDTM/ADaM.

---

## 1. Zielsetzung des Curated Layers

Der Curated Layer überführt technisch korrekt geladene Staging-Daten in fachlich
plausible, analysierbare und CDISC-kompatible Entitäten.

Während Staging nur sicherstellt, dass Daten geladen wurden, erzwingt Curated:
- Typensicherheit (CAST auf DATE, TIMESTAMP, DOUBLE)
- Zeitliche Plausibilität (Start ≤ Stop)
- Ökonomische Plausibilität (keine negativen Kosten)
- Primär-/Fremdschlüssel-Integrität
- Auditierbare Ausschlusslogik (Reject-Tabellen)

---

## 2. Architektur-Kontext

Raw → Staging → **Curated** → SDTM → ADaM → Marts → ML

Curated ist das zentrale Qualitäts-Gate vor dem CDISC Mapping.

---

## 3. Quarantäne-Pattern

Für jede Entität:
- `cur_*` → valide Datensätze
- `rej_*` → verworfene Datensätze mit `reject_reason`

So bleibt jeder Ausschluss nachvollziehbar (Audit Trail).

---

## 4. Entitäten: Transformationen & Regeln

### 4.1 Patients → cur_patients / rej_patients
**Ziel:** stabile Personenbasis für SDTM DM.

**Transformationen**
- Id → patient_id
- BirthDate/DeathDate → DATE
- Income/Healthcare_* → DOUBLE
- technische Spalten (_run_id, _ingested_at) bleiben erhalten

**Validierung**
| Regel | Begründung |
|---|---|
| patient_id nicht NULL | Primärschlüssel |

**Rejects**
- missing_patient_id

---

### 4.2 Encounters → cur_encounters / rej_encounters
**Ziel:** Visit/Stay-Entität; Grundlage für SDTM SV und LOS.

**Transformationen**
- Start/Stop → TIMESTAMP (cast-safe)
- Base_Encounter_Cost/Total_Claim_Cost/Payer_Coverage → DOUBLE
- Ableitung `los_days = DATE_DIFF('day', start_ts, stop_ts)` (nur wenn Start/Stop valide)

**Validierung**
| Regel | Begründung |
|---|---|
| encounter_id nicht NULL | PK |
| patient_id nicht NULL | FK |
| start_ts ≤ stop_ts | Zeitplausibilität |
| base_cost ≥ 0 | Ökonomische Plausibilität |
| total_cost ≥ 0 | Ökonomische Plausibilität |

**Rejects**
- missing_encounter_id
- missing_patient_id
- bad_time_order_start_gt_stop
- negative_base_cost
- negative_total_cost

---

### 4.3 Conditions → cur_conditions_ri (+ rejects)
**Ziel:** Grundlage für SDTM MH; RI-clean.

**Validierung**
- patient_id muss existieren
- encounter_id optional, aber wenn vorhanden, muss existieren
- Zeitfelder castbar; Stop ≥ Start falls vorhanden

---

### 4.4 Observations → cur_observations_ri (+ rejects)
**Ziel:** Grundlage für SDTM LB/VS.

**Transformationen**
- Date → TIMESTAMP
- Value (wo möglich) → numeric
- LOINC Code/Description übernehmen

**Validierung**
- patient_id vorhanden
- datum castbar

---

### 4.5 Medications → cur_medications_ri (+ rejects)
**Ziel:** Grundlage SDTM CM.

**Validierung**
- patient_id vorhanden
- Start/Stop plausibel (falls vorhanden)
- Kosten ≥ 0 (falls vorhanden)

---

### 4.6 Procedures → cur_procedures_ri (+ rejects)
**Ziel:** Grundlage SDTM PR.

**Validierung**
- patient_id vorhanden
- datum castbar

---

## 5. Fachliche Ableitungen

### 5.1 LOS (Length of Stay)
- wird im Curated Layer technisch berechnet, aber **nicht imputiert**
- Imputation und Analyse-Flags kommen später in ADaM (Step 08)

---

## 6. Outputs

**Curated Tables**
- cur_patients
- cur_encounters
- cur_conditions_ri
- cur_observations_ri
- cur_medications_ri
- cur_procedures_ri

**Reject Tables**
- rej_patients, rej_encounters, …

**Reports**
- HTML QC Report
- JSON Results
- manifest_curated.json

---

## 7. Warum Step 05 kritisch ist

Ohne Curated:
- SDTM Mapping erzeugt Orphans & falsche Visits
- ADaM LOS-Berechnung wird unzuverlässig
- ML trainiert auf inkonsistenten Daten

Curated ist somit das zentrale Data Quality Gate vor CDISC.
