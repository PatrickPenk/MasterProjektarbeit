# Step 06 – SDTM Mapping (Curated → SDTM)
**Layer:** Curated → SDTM  
**Standard:** CDISC SDTM  
**Ziel:** Erzeugung standardisierter SDTM-Domains aus den bereinigten Curated-Entitäten.

---

## 1. Zweck und Rolle

Dieser Schritt transformiert die projektspezifischen Curated Tabellen in
CDISC-konforme SDTM Domains. Er ist der “Standardisierungs”-Step, der
Interoperabilität und regulatorisch bekannte Struktur herstellt.

Zentrale Ziele:
- einheitliche Subjekt-Identität (USUBJID)
- konsistente STUDYID/DOMAIN-Spalten
- domain-spezifische Struktur (SV/LB/VS/MH/CM/PR)
- Zeitfelder als SDTM-konforme DTC Strings (ISO-ish)

---

## 2. Preconditions (Eingabe-Erwartungen)

Der Step erwartet mindestens:
- cur_patients
- cur_encounters_ri
- cur_conditions_ri
- cur_observations_ri
- cur_medications_ri
- cur_procedures_ri

Wenn diese fehlen, ist Step 05 nicht erfolgreich gelaufen.

---

## 3. Globale Konventionen

### 3.1 STUDYID
- Standardwert: SYNTH-01 (oder aus Manifest ableitbar)

### 3.2 USUBJID
- Konstruktion: "SYN-" || patient_id
- Ziel: stabile Subjekt-ID über alle Domains hinweg

### 3.3 DOMAIN
- pro Domain fix (DM, SV, LB, VS, MH, CM, PR)

---

## 4. Domain-Erstellung (Mapping-Logik)

### 4.1 DM – Demographics
Quelle: cur_patients  
Wesentliche Felder:
- USUBJID aus patient_id
- SEX aus gender (M/F/U)
- BRTHDTC / DTHDTC als String
- RACE, ETHNIC

Qualitätsannahme:
- patient_id non-null (aus Curated Gate)

---

### 4.2 SV – Subject Visits
Quelle: cur_encounters_ri  
Abbildung:
- VISIT/VISITNUM aus Encounter-Informationen
- SVSTDTC/SVENDTC aus start_ts/stop_ts

Bedeutung:
- SV ist Zeitanker für spätere ADaM LOS Logik.

---

### 4.3 LB – Laboratory Tests
Quelle: cur_observations_ri  
Abbildung:
- LBTESTCD/LBTEST aus Code/Description
- LBDTC aus observation date
- LBORES / LBNRIND je nach Verfügbarkeit

---

### 4.4 VS – Vital Signs (LOINC-gesteuert)
Quelle: cur_observations_ri  
Speziallogik: Vital Signs werden durch ein **explizites, auditierbares** LOINC-Mapping
abgetrennt (VS_LOINC Dictionary).

Damit entsteht:
- VS nur für definierte Vital Parameter (BP_SYS/BP_DIA/HR/TEMP/…)
- VSTESTCD/VSTEST konsistent zu LOINC-Definitionen

---

### 4.5 MH – Medical History
Quelle: cur_conditions_ri  
- MHTERM aus condition description
- MHSTDTC/MHENDTC aus Start/Stop falls vorhanden

---

### 4.6 CM – Concomitant Medications
Quelle: cur_medications_ri  
- CMTRT aus Medication Name/Description
- CMSTDTC/CMENDTC aus Start/Stop

---

### 4.7 PR – Procedures
Quelle: cur_procedures_ri  
- PRTRT/PRDECOD aus Procedure Description/Code
- PRSTDTC/PRENDTC aus Datum (falls Endedatum vorhanden)

---

## 5. Outputs

### 5.1 SDTM Tabellen in DuckDB
- sdtm_dm, sdtm_sv, sdtm_lb, sdtm_vs, sdtm_mh, sdtm_cm, sdtm_pr

### 5.2 Manifest
- manifest_sdtm.json (run_id, study metadata, erzeugte Tables)

---

## 6. Warum Step 06 prüfungsrelevant ist

- SDTM ist ein Standardformat → Interoperabilität/Auditierbarkeit
- Domänenstruktur ermöglicht standardisierte QC (Step 07)
- ADaM baut direkt auf SDTM Zeitankern (SV) auf
