# ============================================================
# 13_archive_package.py
# ARCHIVIERUNG + ANONYMISIERUNGSPAKET (prüfungs-/audit-tauglich)
#
# Zweck:
#  - erstellt ein versioniertes Archivpaket pro Run
#  - sammelt Manifeste, Reports, Marts, ML-Outputs
#  - anonymisiert/pseudonymisiert CSVs (USUBJID -> Hash, sensible Spalten raus)
#  - baut optional eine *anonymisierte* DuckDB (nur de-identified Tabellen)
#  - erzeugt inventory.csv + checksums.sha256 + metadata.json + README
#  - optional: ZIP des Pakets
#
# Laufposition: nach Step 12 (final report)
#
# Outputs:
#  - outputs/archive/archive_<run_id>_<ts>/...
#  - outputs/archive/archive_<run_id>_<ts>.zip   (optional)
# ============================================================

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd

from scripts.config import OUT_DIR, DB_PATH, ensure_dirs

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 13 – ARCHIVIERUNGSPAKET (de-identified) + Checksums")
print("=" * 70)

RUN_TS = datetime.now(timezone.utc)

ARCHIVE_ROOT = OUT_DIR / "archive"
ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------
# 0) Policy / Settings
# ------------------------------------------------------------

# ZIP erzeugen
MAKE_ZIP = True

# De-identified DuckDB erzeugen (empfohlen, statt Full DB Snapshot)
INCLUDE_ANON_DB = True

# Welche Tabellen sollen in die anonymisierte DB?
# Default: alle mart_* Tabellen, plus optional adam_adsl (wenn du willst)
ANON_DB_TABLE_PATTERNS = [
    r"^mart_.*$",
    # r"^adam_adsl$",   # optional: nur wenn wirklich nötig
]

# Anonymisierung: Salt
# Für reproduzierbare Pseudonyme kann das Salt als ENV gesetzt werden:
#   set ARCHIVE_SALT="dein_salt"
# Wenn nicht gesetzt: wird zufällig aus RunID+Timestamp generiert.
ARCHIVE_SALT_ENV = "ARCHIVE_SALT"

# Soll das Salt im Archiv gespeichert werden?
# Externes Sharing: False (empfohlen). Intern: True möglich.
STORE_SALT_IN_ARCHIVE = False

# Spalten, die wir sehr wahrscheinlich entfernen wollen (direkte Identifikatoren)
DROP_COL_REGEX = re.compile(
    r"(name|email|e-mail|phone|tel|address|street|zip|postal|city|state|mrn|patid|patientid|ssn|social|passport|iban|account)",
    re.IGNORECASE,
)

# Datums-/Zeit-Spalten (für externen Export meist raus)
DROP_DATE_COL_REGEX = re.compile(
    r"(dtc|date|datetime|timestamp|_ts$|_dt$|visit_date|admit|discharge)",
    re.IGNORECASE,
)

# Schlüsselfelder, die pseudonymisiert werden sollen
PSEUDONYM_KEYS = ["USUBJID"]  # falls weitere IDs auftauchen, hier ergänzen

# ------------------------------------------------------------
# 1) Helpers
# ------------------------------------------------------------

def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True

def copy_tree_if_exists(src_dir: Path, dst_dir: Path, patterns: Optional[List[str]] = None) -> int:
    if not src_dir.exists() or not src_dir.is_dir():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)

    files: List[Path] = []
    if patterns:
        for pat in patterns:
            files.extend(src_dir.rglob(pat))
    else:
        files = [p for p in src_dir.rglob("*") if p.is_file()]

    n = 0
    for f in files:
        rel = f.relative_to(src_dir)
        out = dst_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        n += 1
    return n

def find_run_id_from_manifests(out_dir: Path) -> str:
    candidates = [
        out_dir / "final" / "final_summary.json",
        out_dir / "manifest_ml.json",
        out_dir / "manifest_marts.json",
        out_dir / "manifest_adam_checked.json",
        out_dir / "manifest_sdtm_checked.json",
        out_dir / "manifest_raw.json",
    ]
    for p in candidates:
        if p.exists():
            d = load_json(p)
            if isinstance(d, dict):
                rid = d.get("run_id") or d.get("Run-ID")
                if rid:
                    return str(rid)
                for k in ("ml", "marts", "adam_qc", "sdtm_qc"):
                    rid2 = (d.get(k) or {}).get("run_id")
                    if rid2:
                        return str(rid2)
    return "UNBEKANNT"

def get_salt(run_id: str, ts_tag: str) -> str:
    s = os.environ.get(ARCHIVE_SALT_ENV)
    if s and s.strip():
        return s.strip()
    # deterministisch genug pro Archiv, aber ohne ENV nicht wiederverwendbar
    return f"salt::{run_id}::{ts_tag}::{os.getpid()}"

def pseudonymize_value(val: Any, salt: str) -> Optional[str]:
    if val is None:
        return None
    s = str(val)
    if s.lower() in ("", "none", "nan"):
        return None
    digest = hashlib.sha256((salt + "::" + s).encode("utf-8")).hexdigest()
    return digest

def anonymize_dataframe(df: pd.DataFrame, salt: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    - USUBJID -> SUBJ_HASH (sha256(salt+USUBJID)), drop original USUBJID
    - drop direct identifier columns by regex
    - drop date/time-ish columns by regex (configurable)
    Returns (anonymized_df, report)
    """
    report: Dict[str, Any] = {
        "n_rows_in": int(len(df)),
        "cols_in": list(df.columns),
        "dropped_cols": [],
        "pseudonymized_cols": [],
    }

    out = df.copy()

    # 1) Drop direct identifier columns
    drop_cols = [c for c in out.columns if DROP_COL_REGEX.search(c or "")]
    if drop_cols:
        out = out.drop(columns=drop_cols, errors="ignore")
        report["dropped_cols"].extend(drop_cols)

    # 2) Pseudonymize keys
    for key in PSEUDONYM_KEYS:
        if key in out.columns:
            out["SUBJ_HASH"] = out[key].apply(lambda x: pseudonymize_value(x, salt))
            out = out.drop(columns=[key], errors="ignore")
            report["pseudonymized_cols"].append(key)

    # 3) Drop date/time columns
    date_cols = [c for c in out.columns if DROP_DATE_COL_REGEX.search(c or "")]
    # nicht SUBJ_HASH droppen falls regex matcht
    date_cols = [c for c in date_cols if c != "SUBJ_HASH"]
    if date_cols:
        out = out.drop(columns=date_cols, errors="ignore")
        report["dropped_cols"].extend(date_cols)

    # 4) Optional: String trimming for safety
    for c in out.columns:
        if out[c].dtype == "object":
            out[c] = out[c].astype(str).replace({"nan": None, "None": None, "": None})

    report["n_rows_out"] = int(len(out))
    report["cols_out"] = list(out.columns)
    return out, report

def matches_any_pattern(name: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if re.search(pat, name):
            return True
    return False


# ------------------------------------------------------------
# 2) Archiv-Ordner vorbereiten
# ------------------------------------------------------------

RUN_ID = find_run_id_from_manifests(OUT_DIR)
TS_TAG = RUN_TS.strftime("%Y%m%dT%H%M%SZ")
SALT = get_salt(RUN_ID, TS_TAG)
SALT_FINGERPRINT = sha256_bytes(SALT.encode("utf-8"))

ARCHIVE_DIR = ARCHIVE_ROOT / f"archive_{RUN_ID}_{TS_TAG}"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

DIR_MANIFESTS = ARCHIVE_DIR / "01_manifests"
DIR_REPORTS   = ARCHIVE_DIR / "02_reports"
DIR_MARTS     = ARCHIVE_DIR / "03_marts_deidentified"
DIR_ML        = ARCHIVE_DIR / "04_ml"
DIR_DB        = ARCHIVE_DIR / "05_db_deidentified"

INVENTORY_CSV = ARCHIVE_DIR / "inventory.csv"
CHECKSUMS_TXT = ARCHIVE_DIR / "checksums.sha256"
META_JSON     = ARCHIVE_DIR / "archive_metadata.json"
README_TXT    = ARCHIVE_DIR / "00_README.txt"
ANON_REPORT   = ARCHIVE_DIR / "pseudonymization_report.json"
RETENTION_TXT = ARCHIVE_DIR / "retention_policy.txt"
FAIR_TXT      = ARCHIVE_DIR / "fair_notes.txt"

# ------------------------------------------------------------
# 3) Einsammeln: Manifeste / Reports / ML
# ------------------------------------------------------------

# 3.1 Manifeste
manifest_files = sorted(OUT_DIR.glob("manifest_*.json"))
n_manifests = 0
for mf in manifest_files:
    if copy_if_exists(mf, DIR_MANIFESTS / mf.name):
        n_manifests += 1

# final summary/report
copy_tree_if_exists(OUT_DIR / "final", DIR_REPORTS / "final", patterns=["*.json", "*.html"])

# QC Reports
copy_tree_if_exists(OUT_DIR / "adam_qc", DIR_REPORTS / "adam_qc", patterns=["*.html", "*.csv", "*.png", "*.json"])
copy_tree_if_exists(OUT_DIR / "sdtm_qc", DIR_REPORTS / "sdtm_qc", patterns=["*.html", "*.csv", "*.png", "*.json"])
copy_tree_if_exists(OUT_DIR / "staging_qc", DIR_REPORTS / "staging_qc", patterns=["*.html", "*.csv", "*.png", "*.json"])

# ML Outputs (falls vorhanden)
for cand in ["ml", "models", "model", "training", "ml_outputs"]:
    p = OUT_DIR / cand
    if p.exists() and p.is_dir():
        copy_tree_if_exists(p, DIR_ML / cand, patterns=["*.csv", "*.json", "*.html", "*.png", "*.txt"])

# ------------------------------------------------------------
# 4) Marts: CSVs anonymisieren und ins Archiv schreiben
# ------------------------------------------------------------

anon_log: Dict[str, Any] = {
    "run_id": RUN_ID,
    "archived_at_utc": RUN_TS.isoformat(),
    "salt_fingerprint_sha256": SALT_FINGERPRINT,
    "store_salt_in_archive": STORE_SALT_IN_ARCHIVE,
    "files": [],
}

marts_src = OUT_DIR / "marts"
marts_dst = DIR_MARTS
marts_dst.mkdir(parents=True, exist_ok=True)

if marts_src.exists() and marts_src.is_dir():
    for csv_path in sorted(marts_src.glob("*.csv")):
        try:
            df = pd.read_csv(csv_path)
            df_anon, rep = anonymize_dataframe(df, salt=SALT)
            out_path = marts_dst / csv_path.name
            df_anon.to_csv(out_path, index=False)
            anon_log["files"].append(
                {
                    "input": csv_path.as_posix(),
                    "output": out_path.as_posix(),
                    "report": rep,
                }
            )
        except Exception as e:
            anon_log["files"].append(
                {
                    "input": csv_path.as_posix(),
                    "output": None,
                    "error": str(e),
                }
            )

ANON_REPORT.write_text(json.dumps(anon_log, indent=2, ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------
# 5) Optional: Anonymisierte DuckDB bauen (nur de-identified Tabellen)
# ------------------------------------------------------------

anon_db_path = None
anon_db_tables: List[str] = []

if INCLUDE_ANON_DB:
    try:
        import duckdb

        DIR_DB.mkdir(parents=True, exist_ok=True)
        anon_db_path = DIR_DB / "anonymized.duckdb"

        # neue DB erstellen/überschreiben
        if anon_db_path.exists():
            anon_db_path.unlink()

        src = duckdb.connect(str(DB_PATH))
        dst = duckdb.connect(str(anon_db_path))

        try:
            # alle Tabellen im Source holen
            tbls = [r[0] for r in src.execute("SELECT table_name FROM duckdb_tables()").fetchall()]

            for t in tbls:
                if not matches_any_pattern(t, ANON_DB_TABLE_PATTERNS):
                    continue

                # table -> df -> anonymize -> create in dst
                df = src.execute(f"SELECT * FROM {t}").fetchdf()
                df_anon, rep = anonymize_dataframe(df, salt=SALT)

                # table name beibehalten
                dst.execute(f"DROP TABLE IF EXISTS {t}")
                dst.register("tmp_df", df_anon)
                dst.execute(f"CREATE TABLE {t} AS SELECT * FROM tmp_df")
                dst.unregister("tmp_df")

                anon_db_tables.append(t)

            # zusätzlich: (optional) eine kleine Metatabelle
            meta = pd.DataFrame(
                [{
                    "run_id": RUN_ID,
                    "archived_at_utc": RUN_TS.isoformat(),
                    "salt_fingerprint_sha256": SALT_FINGERPRINT,
                    "tables_included": ", ".join(anon_db_tables),
                }]
            )
            dst.execute("DROP TABLE IF EXISTS archive_meta")
            dst.register("meta_df", meta)
            dst.execute("CREATE TABLE archive_meta AS SELECT * FROM meta_df")
            dst.unregister("meta_df")

        finally:
            try:
                src.close()
            except Exception:
                pass
            try:
                dst.close()
            except Exception:
                pass

    except Exception as e:
        # schreibe Fehler in anonymization_report
        anon_log["anon_db_error"] = str(e)
        ANON_REPORT.write_text(json.dumps(anon_log, indent=2, ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------
# 6) README / FAIR / Retention
# ------------------------------------------------------------

README_TXT.write_text(
    "\n".join([
        "ARCHIVPAKET – Projektarbeit Datenpipeline (de-identified)",
        "",
        f"Run-ID: {RUN_ID}",
        f"Timestamp (UTC): {RUN_TS.isoformat()}",
        "",
        "Zweck:",
        " - Nachvollziehbare Archivierung eines Pipeline-Runs inkl. Qualitätsnachweisen und Ergebnissen.",
        " - Extern teilbar durch De-Identification (Pseudonymisierung).",
        "",
        "Inhalt:",
        " - 01_manifests: Pipeline-Manifeste (Provenienz / Run-Nachweise)",
        " - 02_reports: QC- und Abschlussreports (HTML/CSV/PNG)",
        " - 03_marts_deidentified: anonymisierte Analyse-Datensätze (CSV)",
        " - 04_ml: Modelltrainingsergebnisse (falls vorhanden)",
        " - 05_db_deidentified: anonymisierte DuckDB (nur ausgewählte Tabellen)",
        "",
        "De-Identification:",
        " - USUBJID wird ersetzt durch SUBJ_HASH = SHA256(salt + USUBJID).",
        " - direkte Identifikatoren (name/email/...) werden entfernt (heuristisch).",
        " - Datums-/Timestamp-Spalten werden entfernt (heuristisch).",
        f" - Salt-Fingerprint (SHA256): {SALT_FINGERPRINT}",
        "",
        "Integrität:",
        " - inventory.csv enthält Dateiübersicht + SHA256",
        " - checksums.sha256 enthält SHA256 Summen",
        "",
        "Hinweis:",
        " - Dies ist Pseudonymisierung. Re-Identifikation ist bei Quasi-Identifiern prinzipiell möglich.",
        " - Für externe Weitergabe ist eine Datenschutzprüfung empfohlen.",
    ]),
    encoding="utf-8",
)

FAIR_TXT.write_text(
    "\n".join([
        "FAIR NOTIZEN (kurz)",
        "",
        "Findable:",
        "- Archivordner enthält eindeutige Run-ID + Timestamp.",
        "- inventory.csv listet alle Dateien.",
        "",
        "Accessible:",
        "- Paket ist strukturiert (manifests/reports/marts/ml/db).",
        "- Zugriff kann über Berechtigungen geregelt werden (extern nur de-identified).",
        "",
        "Interoperable:",
        "- CSV als Austauschformat; JSON Manifeste; HTML Reports.",
        "",
        "Reusable:",
        "- Provenienz über manifest_*.json + final_summary.json.",
        "- Anonymisierungsreport dokumentiert Transformationen.",
    ]),
    encoding="utf-8",
)

RETENTION_TXT.write_text(
    "\n".join([
        "RETENTION POLICY (Beispiel – anpassbar)",
        "",
        "Empfehlung:",
        "- de-identified Archivpaket: 10 Jahre (wissenschaftliche Nachvollziehbarkeit).",
        "- interne Rohdaten/volle DB (falls vorhanden): nach institutioneller Policy, Zugriff streng beschränkt.",
        "",
        "Lösch-/Review-Regel:",
        "- jährliche Prüfung, ob Aufbewahrung weiterhin erforderlich ist.",
        "- bei Wegfall des Zwecks: Löschung/Weiteranonymisierung.",
    ]),
    encoding="utf-8",
)

# Optional: Salt speichern (intern)
if STORE_SALT_IN_ARCHIVE:
    (ARCHIVE_DIR / "salt.txt").write_text(SALT, encoding="utf-8")

# ------------------------------------------------------------
# 7) Inventory + Checksums
# ------------------------------------------------------------

all_files = [p for p in ARCHIVE_DIR.rglob("*") if p.is_file() and p.name not in ["checksums.sha256"]]

with INVENTORY_CSV.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["relative_path", "size_bytes", "sha256"])
    for p in sorted(all_files):
        rel = p.relative_to(ARCHIVE_DIR).as_posix()
        size = p.stat().st_size
        digest = sha256_file(p)
        w.writerow([rel, size, digest])

with CHECKSUMS_TXT.open("w", encoding="utf-8") as f:
    for p in sorted(all_files):
        rel = p.relative_to(ARCHIVE_DIR).as_posix()
        digest = sha256_file(p)
        f.write(f"{digest}  {rel}\n")

# ------------------------------------------------------------
# 8) Metadata
# ------------------------------------------------------------

metadata = {
    "run_id": RUN_ID,
    "archived_at_utc": RUN_TS.isoformat(),
    "out_dir": OUT_DIR.as_posix(),
    "db_path_source": DB_PATH.as_posix(),
    "deidentified": {
        "salt_fingerprint_sha256": SALT_FINGERPRINT,
        "store_salt_in_archive": STORE_SALT_IN_ARCHIVE,
        "pseudonym_keys": PSEUDONYM_KEYS,
        "drop_col_regex": DROP_COL_REGEX.pattern,
        "drop_date_col_regex": DROP_DATE_COL_REGEX.pattern,
        "anon_db_enabled": INCLUDE_ANON_DB,
        "anon_db_path": anon_db_path.as_posix() if anon_db_path else None,
        "anon_db_tables": anon_db_tables,
        "anon_report": ANON_REPORT.as_posix(),
    },
    "counts": {
        "manifests": n_manifests,
        "files_total": len(all_files),
    },
    "structure": {
        "manifests_dir": DIR_MANIFESTS.as_posix(),
        "reports_dir": DIR_REPORTS.as_posix(),
        "marts_dir": DIR_MARTS.as_posix(),
        "ml_dir": DIR_ML.as_posix(),
        "db_dir": DIR_DB.as_posix(),
    },
    "integrity": {
        "inventory_csv": INVENTORY_CSV.as_posix(),
        "checksums_sha256": CHECKSUMS_TXT.as_posix(),
    },
}

META_JSON.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------
# 9) ZIP erstellen
# ------------------------------------------------------------

zip_path = ARCHIVE_ROOT / f"archive_{RUN_ID}_{TS_TAG}.zip"
if MAKE_ZIP:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted([q for q in ARCHIVE_DIR.rglob("*") if q.is_file()]):
            z.write(p, arcname=p.relative_to(ARCHIVE_DIR).as_posix())

# ------------------------------------------------------------
# 10) Console summary
# ------------------------------------------------------------

print("\n" + "-" * 70)
print("ARCHIVE – SUMMARY")
print("-" * 70)
print(f"[run] run_id                     : {RUN_ID}")
print(f"[out] archive_dir                : {ARCHIVE_DIR}")
print(f"[out] inventory.csv              : {INVENTORY_CSV}")
print(f"[out] checksums.sha256           : {CHECKSUMS_TXT}")
print(f"[out] metadata.json              : {META_JSON}")
print(f"[out] pseudonymization_report.json  : {ANON_REPORT}")
print(f"[anon] salt_fingerprint_sha256   : {SALT_FINGERPRINT}")
print(f"[anon] anon_db_enabled           : {INCLUDE_ANON_DB}")
if anon_db_path:
    print(f"[anon] anon_db_path              : {anon_db_path}")
    print(f"[anon] anon_db_tables            : {', '.join(anon_db_tables) if anon_db_tables else '(none)'}")
if MAKE_ZIP:
    print(f"[out] zip                       : {zip_path}")
print(f"[cnt] total files                : {len(all_files)}")
print("=" * 70 + "\n")
print("STEP 13 DONE")
