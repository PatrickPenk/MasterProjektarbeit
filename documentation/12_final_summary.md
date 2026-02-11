# Step 12 – Final Report (HTML + JSON)
**Layer:** Reporting  
**Ziel:** Konsolidierter Projektbericht über Pipeline-Status, QC-Ergebnisse, Artefakte und ML-Resultate.

---

## 1. Zweck und Rolle

Step 12 ist der “Single Source of Truth” Bericht für die Abgabe:
- Welche Pipeline-Phasen wurden erfolgreich ausgeführt?
- Welche QC-Ergebnisse liegen vor?
- Welche Marts/Modelle/Charts wurden erzeugt?
- Wo liegen die Artefakte (für Reviewer/Prüfer)?

Er verbindet technische Outputs (Manifest/CSV/Charts) zu einer lesbaren Enddokumentation.

---

## 2. Input: Manifeste einsammeln
Sammelt (wenn vorhanden):
- manifest_raw.json
- manifest_staging_checked.json
- manifest_sdtm_checked.json
- manifest_adam_checked.json
- manifest_marts.json
- manifest_ml.json

Fehlende Phasen werden als “Fehlt / nicht ausgeführt” markiert.

---

## 3. Run-ID Ermittlung (robust)
Da run_id in unterschiedlichen Manifests an verschiedenen Stellen liegen kann:
- deep_find_run_id durchsucht verschachtelte Strukturen nach gängigen Keys
- Priorität: ML > Marts > ADaM > SDTM > Raw

Ziel:
- konsistente Zuordnung der gesamten Pipeline zu einem Run.

---

## 4. Report-Inhalte (typisch)

### 4.1 Pipeline Status
- Phasenliste + Status

### 4.2 QC Zusammenfassungen
- SDTM QC Kennzahlen
- ADaM QC Kennzahlen

### 4.3 Marts Übersicht
- welche CSV/Tabellen erzeugt wurden

### 4.4 ML Ergebnisse
- beste Metriken
- Modellartefakte
- Charts

---

## 5. Rendering-Logik
- HTML Tables für strukturierte Abschnitte
- relative Pfade von `out/final/` auf Artefakte, damit Report “portable” bleibt
- Charts werden als `<img>` eingebunden

---

## 6. Outputs
- `out/final/final_summary.json`
- `out/final/final_report.html`

---

## 7. Bedeutung für Abgabe
Step 12 liefert:
- reproduzierbare Nachweise (QC + ML)
- klare Referenzen auf Artefakte
- Management-/Reviewer-taugliche Übersicht
