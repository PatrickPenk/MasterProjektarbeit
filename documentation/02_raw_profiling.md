# Step 02 – Raw Profiling (Exploratives Data Profiling)
**Layer:** Raw  
**Ziel:** Frühe Sichtbarkeit von Qualitätsrisiken (Missingness, Duplikate, Datums-Parse-Probleme), bevor Daten in DuckDB geladen und transformiert werden.

---

## 1. Zweck und Rolle

Raw Profiling ist kein “Gate” wie spätere QC-Schritte, sondern ein:
- **Frühwarnsystem** für strukturelle Probleme,
- **Baseline** für Datenqualität (vor Transformation),
- und liefert evidenzbasierte Hinweise für spätere Curated/SDTM-Regeln.

---

## 2. Scope / Tabellen-Auswahl

### 2.1 Core Tables Contract
Es existiert eine definierte Liste von Kern-Tabellen. Der Schritt kann:
- strikt sein (abbrechen wenn Core fehlt) oder
- tolerant sein (nur vorhandene Tabellen profilieren).

### 2.2 Profiling-Modus
- Standard: Profiling nur der Core Tables (reduziert Laufzeit, Fokus auf “wichtige” Entitäten).

---

## 3. Profiling-Metriken (je Tabelle)

### 3.1 Struktur
- `n_rows`, `n_cols`
- Hilft bei Größenordnung/Outlier (z.B. leere Tabelle).

### 3.2 Duplikate (row-level)
- Anzahl vollständig duplizierter Zeilen (exact duplicates).
- Hinweis: nicht PK-Duplikate, sondern komplette Zeilen-Duplikate.

### 3.3 Missingness
- Missing-Rate pro Spalte.
- Ausgabe: Top 10 Spalten mit höchster Missing-Rate.
- Zweck: Identifikation “quasi leerer” Felder.

### 3.4 Datumsfelder: Erkennung + Parse-Qualität
- Heuristische Erkennung von Date-Spalten (z.B. “start”, “stop”, “birth”, “death”, “date”, “dt”).
- Parsing via `to_datetime(errors="coerce")`.
- Kennzahl: Anteil nicht parsebarer Werte unter den nicht-NULL-Werten.

Interpretation:
- hohe Parse-Fail-Rate → Formatprobleme, inkonsistente Eingaben oder falsche Spaltenerkennung.

---

## 4. Outputs / Artefakte

### 4.1 Profiling Summary
- `raw_profiling_summary.csv`  
enthält pro Tabelle:
- Zeilen/Spalten
- duplicate_rows
- Anzahl erkannter Date-Spalten
- “worst date parse invalid rate”
- Top missing cols (kompakt als String)

### 4.2 Date Parse Details
- `raw_datefield_parse_quality.csv`  
enthält pro (Tabelle, Spalte) den invalid_parse_rate.

### 4.3 Manifest
- typischerweise `manifest_raw_profiled.json` oder Auswertung im Reporting-Kontext.

---

## 5. Qualitäts-/Governance-Nutzen

- **Transparenz:** Rohdatenqualität sichtbar machen, ohne Eingriff.
- **Reproduzierbarkeit:** Profiling-Ergebnisse sind deterministische Outputs.
- **Vorbereitung:** Liefert rationale Basis für QC/Curated-Regeln.

---

## 6. Typische Findings & Implikationen

- “Start/Stop” Spalten teilweise nicht parsebar → später Cast-Safe Transform nötig.
- hohe Missingness in klinischen Feldern → Feature Coverage Policy (später Step 10).
- viele Zeilen-Duplikate → Warnsignal für PK/De-Dup in Curated/QC.
