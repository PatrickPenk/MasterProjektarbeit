# Step 13 – Archivierung (KW5: SIP → AIP → DIP, Fixity, BagIt-like)
**Layer:** Archiv / Langzeitkonzept  
**Ziel:** Erstellung eines Archivpakets nach OAIS-Idee inkl. Fixity, pseudonymisierter Access-Copy und exportfähigem AIP.

---

## 1. Zweck und Rolle

Step 13 operationalisiert Forschungsdatenarchivierung:
- Sammlung der Projektartefakte (SIP)
- Erzeugung eines archivfähigen Masterpakets (AIP) inkl. Fixity
- Erzeugung einer reduzierten Zugriffskopie (DIP)
- Schutz sensibler Daten durch Pseudonymisierung/Column-Drops

Damit ist das Projekt “KW5-konform” dokumentiert und archivierungsfähig.

---

## 2. Run-ID Auswahl
Da Archivpakete einem Pipeline-Run zugeordnet sein müssen:
- deep search in manifest_*.json + final_summary.json
- Priorität: ML > Marts > ADaM checked > SDTM checked > Staging checked > Raw
- Fallback: deterministische RUN_<timestamp>

Ziel:
- stabile Paket-ID und klare Provenienz.

---

## 3. SIP (Submission Information Package)
Sammelt aus `out/`:
- Manifeste
- Reports (HTML)
- Charts (PNG)
- Marts (CSV)
- ML Artefakte (pkl/json/csv)

SIP ist die “Lieferung” aus dem Projekt in das Archiv.

---

## 4. Datenschutz / Anonymisierung
### 4.1 Drop Identifier Columns (Regex)
Entfernt Spalten, die wie Identifikatoren wirken:
- name, email, phone, address, zip, city, state, patientid, ssn, …

### 4.2 Pseudonymisierung
Ersetzt USUBJID durch:
- SUBJ_HASH = SHA256(salt + USUBJID)

Salt:
- aus ENV `ARCHIVE_SALT` oder deterministisch abgeleitet
- optional: Salt wird nicht ins Archiv geschrieben (Policy)

### 4.3 Drop Date/Time Columns (optional/heuristisch)
Heuristik entfernt Datums-/Zeitfelder (dtc/date/timestamp/…),
wenn die DIP bewusst de-identifiziert sein soll.

Ziel:
- Minimierung re-identifikationsrelevanter Merkmale.

---

## 5. Fixity (Integrität)
Für alle AIP-Dateien:
- SHA256 berechnen
- `manifest-sha256.txt` schreiben
- anschließende Verifikation: `fixity_verification.json`

Ziel:
- Nachweis, dass Archivobjekte unverändert bleiben.

---

## 6. AIP (Archival Information Package)
Erzeugt BagIt-ähnliche Struktur:
- payload (Daten/Artefakte)
- manifests (Fixity)
- metadata (Run-ID, Zeitstempel, Policies)

Export:
- optional tar.gz (AIP_<run>_<ts>.tar.gz)
- optional ZIP Convenience (Policy)

---

## 7. DIP (Dissemination Information Package)
Reduzierte Zugriffskopie:
- fokussiert auf Berichte + ausgewählte CSVs
- pseudonymisiert
- eigenes DIP Manifest

Ziel:
- nutzbar für Dritte/Reviewer ohne Zugriff auf sensible Details.

---

## 8. Outputs
Unter `archive/`:
- AIP Ordnerstruktur
- AIP Export (tar.gz)
- DIP Ordnerstruktur
- Fixity Reports

---

## 9. Bedeutung für Governance/Prüfung
Step 13 zeigt:
- OAIS-konforme Denkweise (SIP/AIP/DIP)
- Fixity als Integritätsnachweis
- Datenschutzmaßnahmen als “Privacy-by-Design”
- archivierungsfähige, nachvollziehbare Projektabgabe
