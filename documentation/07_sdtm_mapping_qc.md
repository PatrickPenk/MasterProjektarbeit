# Step 07 – SDTM Mapping QC (Coverage + Unmapped + Timing + RI + Duplicates)
**Layer:** SDTM  
**Ziel:** Nachweis, dass SDTM-Domains vollständig, konsistent und plausibel sind, bevor ADaM abgeleitet wird.

---

## 1. Zweck und Rolle

Step 07 ist das Qualitäts-Gate für SDTM. Er prüft:
- ob Domains Daten enthalten (Coverage),
- ob codierte Felder sinnvoll belegt sind (Code/Label Coverage),
- ob Mapping-Lücken sichtbar werden (Unmapped),
- ob Zeitlogik plausibel ist (Timing),
- ob Subjekt-Referenzen konsistent sind (RI zu DM),
- ob potentielle Duplikate/Join-Fehler existieren (Duplicate groups).

Diese Checks reduzieren das Risiko von:
- falscher LOS-Berechnung in ADaM,
- “silent errors” im ML-Datensatz,
- und Datenverlust durch fehlerhafte Standardisierung.

---

## 2. Preconditions

Erwartete SDTM Tabellen (mindestens):
- sdtm_dm
- sdtm_sv
- sdtm_lb
- sdtm_vs
- sdtm_mh
- sdtm_cm
- sdtm_pr

Wenn Tabellen fehlen → Step 06 nicht korrekt.

---

## 3. QC-Kategorien im Detail

### 3.1 Coverage Report (pro Domain)
Für jede Domain:
- `N` = Row Count
- optional `code_present_pct` (wenn Code-Spalte existiert)
- optional `label_present_pct` (wenn Label-Spalte existiert)

**Missing-Definition** (robust):
- NULL
- leerer String
- UNKNOWN/UNK (Case-insensitive)

Ziel:
- zeigt sofort, ob Mapping “leer” oder “halb” ist.

---

### 3.2 Top Unmapped Codes (LB/VS)
Für Domains mit Code+Label:
- ermittelt häufige Kombinationen, bei denen:
  - Code fehlt oder
  - Label fehlt oder
  - generische UNKNOWN-Werte auftreten

Ziel:
- Debugging des Mappings (welche Codes sind problematisch).

---

### 3.3 Timing Plausibility (SV/MH/CM/PR)
Für Domains mit Start/End:
- Anteil Fälle, wo End < Start (zeitlich unmöglich)
- Missing End Rate (End NULL)

Ziel:
- verhindert negative Dauer/Fehlinterpretation.
- liefert Input für spätere Imputation (ADaM).

Outputs:
- Timing CSV
- Charts für bad_end_lt_start und missing_end_pct

---

### 3.4 Referential Integrity zu DM (alle Domains außer DM)
Prüft:
- jeder USUBJID in SV/LB/VS/MH/CM/PR muss in DM vorkommen.

Ziel:
- verhindert Orphans (Records ohne Subjektstammsatz).

Output:
- RI CSV + Chart

---

### 3.5 Duplicate Group Heuristics
Domain-spezifische Schlüsselgruppen (Heuristik):
- SV: USUBJID + SVSTDTC + VISIT
- LB: USUBJID + LBDTC + LBTESTCD + LBORRES
- VS: USUBJID + VSDTC + VSTESTCD + VSORRES
- MH: USUBJID + MHSTDTC + MHTERM
- CM: USUBJID + CMSTDTC + CMTRT
- PR: USUBJID + PRSTDTC + PRTRT

Ziel:
- findet Join-Explosionen / Mehrfachableitungen.
- liefert Duplikatgruppen als CSV.

---

## 4. Reporting & Manifest

### Artefakte
- sdtm_mapping_coverage.csv
- sdtm_top_unmapped.csv
- sdtm_timing_plausibility.csv
- sdtm_ri_to_dm_missing_usubjid.csv
- sdtm_duplicate_groups.csv
- HTML Report (tabellarisch)
- mehrere Charts (Coverage/Timing/RI)

### Manifest
- manifest_sdtm_checked.json mit QC Summary

---

## 5. Bedeutung für Step 08 (ADaM)
ADaM (LOS) braucht:
- korrekte SV Start/End Zeitlogik
- konsistente USUBJIDs
- keine massiven Duplikatgruppen

SDTM QC reduziert “silent failures” im ADaM Mapping massiv.
