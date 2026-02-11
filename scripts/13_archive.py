# ============================================================
# 13_archive.py
# KW5-konformes Archiv: SIP -> AIP(BagIt-like) -> AIP export (tar.gz) -> DIP
#
# - nimmt run_id robust aus out/manifest_*.json (deep search)
# - schreibt nach PROJECT_ROOT/archive (NICHT out/archive)
# - sammelt Manifeste, Reports, Marts, ML, optional anon DuckDB
# - Pseudonymisierung von CSVs (USUBJID -> SUBJ_HASH) + heuristische Drops
# - Fixity: manifest-sha256.txt erstellen + verifizieren (fixity_verification.json)
# - AIP: BagIt-ähnliche Struktur + tar.gz Export
# - DIP: reduzierte Access Copy + eigenes Manifest
# ============================================================

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from scripts.config import OUT_DIR, DB_PATH, PROJECT_ROOT, ensure_dirs

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 13 – ARCHIVIERUNG (KW5: SIP→AIP→DIP) + Fixity/BagIt")
print("=" * 70)

# ------------------------------------------------------------
# 0) Settings / Policy
# ------------------------------------------------------------
MAKE_ZIP = True          # optional: convenience zip vom gesamten ARCHIVE_DIR
MAKE_TARGZ = True        # AIP_<run>_<ts>.tar.gz erzeugen

INCLUDE_ANON_DB = True   # anonymisierte DuckDB subset

ANON_DB_TABLE_PATTERNS = [
    r"^mart_.*$",
    # r"^adam_adsl$",  # optional
]

ARCHIVE_SALT_ENV = "ARCHIVE_SALT"
STORE_SALT_IN_ARCHIVE = False

DROP_COL_REGEX = re.compile(
    r"(name|email|e-mail|phone|tel|address|street|zip|postal|city|state|mrn|patid|patientid|ssn|social|passport|iban|account)",
    re.IGNORECASE,
)
DROP_DATE_COL_REGEX = re.compile(
    r"(dtc|date|datetime|timestamp|_ts$|_dt$|visit_date|admit|discharge)",
    re.IGNORECASE,
)

PSEUDONYM_KEYS = ["USUBJID"]

# DIP: bewusst reduziert (anpassbar)
DIP_FINAL_PATTERNS = ["*.json", "*.html"]
DIP_REPORT_PATTERNS = ["*.html", "*.png", "*.csv", "*.json"]
DIP_MART_PATTERNS = ["*.csv"]

# ------------------------------------------------------------
# 1) Helpers
# ------------------------------------------------------------
def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

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

def matches_any_pattern(name: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if re.search(pat, name):
            return True
    return False

def deep_find_run_id(obj: Any) -> Optional[str]:
    """
    Findet run_id in beliebig verschachtelten JSON-Strukturen.
    Unterstützt häufige Varianten.
    """
    if isinstance(obj, dict):
        for k in ("run_id", "Run-ID", "RUN_ID", "runId", "runID"):
            v = obj.get(k)
            if v:
                return str(v)
        for v in obj.values():
            rid = deep_find_run_id(v)
            if rid:
                return rid
    elif isinstance(obj, list):
        for it in obj:
            rid = deep_find_run_id(it)
            if rid:
                return rid
    return None

def pick_run_id_from_out(out_dir: Path) -> str:
    """
    Priorität wie in deiner Step-12 Logik:
    ML > Marts > ADaM checked > SDTM checked > Staging checked > Raw > irgendein manifest_*.json
    """
    preferred = [
        out_dir / "manifest_ml.json",
        out_dir / "manifest_marts.json",
        out_dir / "manifest_adam_checked.json",
        out_dir / "manifest_sdtm_checked.json",
        out_dir / "manifest_staging_checked.json",
        out_dir / "manifest_raw.json",
        out_dir / "final" / "final_summary.json",
    ]

    # 1) bevorzugte Kandidaten
    for p in preferred:
        if p.exists():
            d = load_json(p)
            rid = deep_find_run_id(d)
            if rid:
                return rid

    # 2) fallback: irgendein manifest_*.json
    for p in sorted(out_dir.glob("manifest_*.json")):
        d = load_json(p)
        rid = deep_find_run_id(d)
        if rid:
            return rid

    # 3) notfalls: deterministische Run-ID (nie UNBEKANNT)
    return f"RUN_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

def get_salt(run_id: str, ts_tag: str) -> str:
    s = os.environ.get(ARCHIVE_SALT_ENV)
    if s and s.strip():
        return s.strip()
    return f"salt::{run_id}::{ts_tag}::{os.getpid()}"

def pseudonymize_value(val: Any, salt: str) -> Optional[str]:
    if val is None:
        return None
    s = str(val)
    if s.lower() in ("", "none", "nan"):
        return None
    return hashlib.sha256((salt + "::" + s).encode("utf-8")).hexdigest()

def anonymize_dataframe(df: pd.DataFrame, salt: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    report: Dict[str, Any] = {
        "n_rows_in": int(len(df)),
        "cols_in": list(df.columns),
        "dropped_cols": [],
        "pseudonymized_cols": [],
    }

    out = df.copy()

    # drop direct identifiers
    drop_cols = [c for c in out.columns if DROP_COL_REGEX.search(c or "")]
    if drop_cols:
        out = out.drop(columns=drop_cols, errors="ignore")
        report["dropped_cols"].extend(drop_cols)

    # pseudonymize keys
    for key in PSEUDONYM_KEYS:
        if key in out.columns:
            out["SUBJ_HASH"] = out[key].apply(lambda x: pseudonymize_value(x, salt))
            out = out.drop(columns=[key], errors="ignore")
            report["pseudonymized_cols"].append(key)

    # drop date/time-ish
    date_cols = [c for c in out.columns if DROP_DATE_COL_REGEX.search(c or "")]
    date_cols = [c for c in date_cols if c != "SUBJ_HASH"]
    if date_cols:
        out = out.drop(columns=date_cols, errors="ignore")
        report["dropped_cols"].extend(date_cols)

    # normalize object cols
    for c in out.columns:
        if out[c].dtype == "object":
            out[c] = out[c].astype(str).replace({"nan": None, "None": None, "": None})

    report["n_rows_out"] = int(len(out))
    report["cols_out"] = list(out.columns)
    return out, report

# ---------------- Fixity (create + verify) ------------------
def write_sha256_manifest(root_dir: Path, manifest_path: Path, rel_base: Path) -> int:
    files = [p for p in root_dir.rglob("*") if p.is_file()]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as f:
        for p in sorted(files):
            rel = p.relative_to(rel_base).as_posix()
            f.write(f"{sha256_file(p)}  {rel}\n")
    return len(files)

def verify_sha256_manifest(rel_base: Path, manifest_path: Path) -> Dict[str, Any]:
    res = {"ok": True, "checked": 0, "missing": [], "mismatch": [], "errors": []}
    if not manifest_path.exists():
        return {"ok": False, "checked": 0, "missing": [], "mismatch": [], "errors": ["manifest not found"]}

    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            digest, rel = line.split(None, 1)
            rel = rel.strip()
            if rel.startswith("./"):
                rel = rel[2:]
            file_path = rel_base / rel
            if not file_path.exists():
                res["ok"] = False
                res["missing"].append(rel)
                continue
            got = sha256_file(file_path)
            if got != digest:
                res["ok"] = False
                res["mismatch"].append({"path": rel, "expected": digest, "got": got})
            res["checked"] += 1
        except Exception as e:
            res["ok"] = False
            res["errors"].append(str(e))
    return res

# ------------------------------------------------------------
# 2) Prepare archive folders (ROOT = PROJECT_ROOT/archive)
# ------------------------------------------------------------
RUN_TS = datetime.now(timezone.utc)
TS_TAG = RUN_TS.strftime("%Y%m%dT%H%M%SZ")
RUN_ID = pick_run_id_from_out(OUT_DIR)

SALT = get_salt(RUN_ID, TS_TAG)
SALT_FINGERPRINT = sha256_text(SALT)

ARCHIVE_ROOT = PROJECT_ROOT / "archive"  # <-- das ist dein gewünschter Root
ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

ARCHIVE_DIR = ARCHIVE_ROOT / f"archive_{RUN_ID}_{TS_TAG}"
if ARCHIVE_DIR.exists():
    shutil.rmtree(ARCHIVE_DIR)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# SIP-like layout
DIR_MANIFESTS = ARCHIVE_DIR / "01_manifests"
DIR_REPORTS   = ARCHIVE_DIR / "02_reports"
DIR_MARTS     = ARCHIVE_DIR / "03_marts_deidentified"
DIR_ML        = ARCHIVE_DIR / "04_ml"
DIR_DB        = ARCHIVE_DIR / "05_db_deidentified"
DIR_META      = ARCHIVE_DIR / "06_metadata"

README_TXT    = ARCHIVE_DIR / "00_README.txt"
RETENTION_TXT = ARCHIVE_DIR / "retention_policy.txt"
FAIR_TXT      = ARCHIVE_DIR / "fair_notes.txt"

ANON_REPORT   = DIR_META / "pseudonymization_report.json"
META_JSON     = DIR_META / "archive_metadata.json"
FIXITY_VERIFY = DIR_META / "fixity_verification.json"

INVENTORY_CSV = ARCHIVE_DIR / "inventory.csv"
CHECKSUMS_TXT = ARCHIVE_DIR / "checksums.sha256"

# AIP BagIt-like
AIP_BAG_DIR     = ARCHIVE_DIR / "AIP_bag"
AIP_DATA_DIR    = AIP_BAG_DIR / "data"
AIP_BAGIT_TXT   = AIP_BAG_DIR / "bagit.txt"
AIP_BAGINFO_TXT = AIP_BAG_DIR / "bag-info.txt"
AIP_MANIFEST    = AIP_BAG_DIR / "manifest-sha256.txt"

# DIP Access Copy
DIP_DIR      = ARCHIVE_DIR / "DIP_access_copy"
DIP_MANIFEST = DIP_DIR / "manifest-sha256.txt"

DIR_META.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------
# 3) Collect: Manifests / Reports / ML
# ------------------------------------------------------------
# Manifests: nimm alle manifest_*.json aus OUT_DIR
n_manifests = 0
for mf in sorted(OUT_DIR.glob("manifest_*.json")):
    if copy_if_exists(mf, DIR_MANIFESTS / mf.name):
        n_manifests += 1

# Final report
copy_tree_if_exists(OUT_DIR / "final", DIR_REPORTS / "final", patterns=["*.json", "*.html"])

# QC Reports
copy_tree_if_exists(OUT_DIR / "adam_qc", DIR_REPORTS / "adam_qc", patterns=["*.html", "*.csv", "*.png", "*.json"])
copy_tree_if_exists(OUT_DIR / "sdtm_qc", DIR_REPORTS / "sdtm_qc", patterns=["*.html", "*.csv", "*.png", "*.json"])
copy_tree_if_exists(OUT_DIR / "staging_qc", DIR_REPORTS / "staging_qc", patterns=["*.html", "*.csv", "*.png", "*.json"])

# ML Outputs
for cand in ["ml", "models", "model", "training", "ml_outputs"]:
    p = OUT_DIR / cand
    if p.exists() and p.is_dir():
        copy_tree_if_exists(p, DIR_ML / cand, patterns=["*.csv", "*.json", "*.html", "*.png", "*.txt"])

# ------------------------------------------------------------
# 4) Marts: anonymisieren -> 03_marts_deidentified
# ------------------------------------------------------------
anon_log: Dict[str, Any] = {
    "run_id": RUN_ID,
    "archived_at_utc": RUN_TS.isoformat(),
    "salt_fingerprint_sha256": SALT_FINGERPRINT,
    "store_salt_in_archive": STORE_SALT_IN_ARCHIVE,
    "files": [],
}

marts_src = OUT_DIR / "marts"
DIR_MARTS.mkdir(parents=True, exist_ok=True)

if marts_src.exists() and marts_src.is_dir():
    for csv_path in sorted(marts_src.glob("*.csv")):
        try:
            df = pd.read_csv(csv_path)
            df_anon, rep = anonymize_dataframe(df, salt=SALT)
            out_path = DIR_MARTS / csv_path.name
            df_anon.to_csv(out_path, index=False)
            anon_log["files"].append({"input": csv_path.as_posix(), "output": out_path.as_posix(), "report": rep})
        except Exception as e:
            anon_log["files"].append({"input": csv_path.as_posix(), "output": None, "error": str(e)})

ANON_REPORT.write_text(json.dumps(anon_log, indent=2, ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------
# 5) Optional: anonymisierte DuckDB subset
# ------------------------------------------------------------
anon_db_path = None
anon_db_tables: List[str] = []

if INCLUDE_ANON_DB:
    try:
        import duckdb

        DIR_DB.mkdir(parents=True, exist_ok=True)
        anon_db_path = DIR_DB / "anonymized.duckdb"
        if anon_db_path.exists():
            anon_db_path.unlink()

        src = duckdb.connect(str(DB_PATH))
        dst = duckdb.connect(str(anon_db_path))
        try:
            tbls = [r[0] for r in src.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
            for t in tbls:
                if not matches_any_pattern(t, ANON_DB_TABLE_PATTERNS):
                    continue
                df = src.execute(f"SELECT * FROM {t}").fetchdf()
                df_anon, _ = anonymize_dataframe(df, salt=SALT)

                dst.execute(f"DROP TABLE IF EXISTS {t}")
                dst.register("tmp_df", df_anon)
                dst.execute(f"CREATE TABLE {t} AS SELECT * FROM tmp_df")
                dst.unregister("tmp_df")

                anon_db_tables.append(t)
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
        anon_log["anon_db_error"] = str(e)
        ANON_REPORT.write_text(json.dumps(anon_log, indent=2, ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------
# 6) README / FAIR / Retention + optional Salt
# ------------------------------------------------------------
README_TXT.write_text(
    "\n".join([
        "ARCHIVPAKET – Projektarbeit Datenpipeline (KW5: SIP→AIP→DIP, de-identified)",
        "",
        f"Run-ID: {RUN_ID}",
        f"Timestamp (UTC): {RUN_TS.isoformat()}",
        "",
        "Struktur:",
        " - 01_manifests: manifest_*.json (Provenienz / Run-Nachweise)",
        " - 02_reports: QC Reports + final report",
        " - 03_marts_deidentified: anonymisierte Marts (CSV)",
        " - 04_ml: ML Outputs (falls vorhanden)",
        " - 05_db_deidentified: anonymisierte DuckDB (Subset)",
        " - 06_metadata: metadata + pseudonymization report + fixity verification",
        " - AIP_bag: BagIt-ähnliches AIP (data/ + manifest-sha256.txt)",
        " - DIP_access_copy: reduzierte Access Copy + eigenes Manifest",
        "",
        "De-Identification:",
        " - USUBJID -> SUBJ_HASH (SHA256(salt + USUBJID))",
        " - direkte Identifikatoren & Datumsfelder heuristisch entfernt",
        f" - Salt-Fingerprint (SHA256): {SALT_FINGERPRINT}",
    ]),
    encoding="utf-8",
)

FAIR_TXT.write_text(
    "\n".join([
        "FAIR NOTIZEN (kurz)",
        "",
        "Findable: Run-ID+Timestamp; inventory/checksums; (extern: DOI via Zenodo möglich).",
        "Accessible: DIP_access_copy als teilbares Paket; Zugriff per Policy/ACL.",
        "Interoperable: CSV/JSON/HTML (+ optional DuckDB).",
        "Reusable: Manifeste + QC Reports + Fixity Verification.",
    ]),
    encoding="utf-8",
)

RETENTION_TXT.write_text(
    "\n".join([
        "RETENTION POLICY (Beispiel)",
        "",
        "- de-identified Archivpaket: 10 Jahre (Nachvollziehbarkeit).",
        "- interne Rohdaten/volle DB: nach Institution, Zugriff beschränkt.",
        "- jährliche Review: Zweck noch gegeben? sonst löschen/weiter anonymisieren.",
    ]),
    encoding="utf-8",
)

if STORE_SALT_IN_ARCHIVE:
    (DIR_META / "salt.txt").write_text(SALT, encoding="utf-8")

# ------------------------------------------------------------
# 7) Inventory + checksums (archive-level)
# ------------------------------------------------------------
all_files = [p for p in ARCHIVE_DIR.rglob("*") if p.is_file() and p.name != "checksums.sha256"]

with INVENTORY_CSV.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["relative_path", "size_bytes", "sha256"])
    for p in sorted(all_files):
        rel = p.relative_to(ARCHIVE_DIR).as_posix()
        w.writerow([rel, p.stat().st_size, sha256_file(p)])

with CHECKSUMS_TXT.open("w", encoding="utf-8") as f:
    for p in sorted(all_files):
        rel = p.relative_to(ARCHIVE_DIR).as_posix()
        f.write(f"{sha256_file(p)}  {rel}\n")

# technical metadata
META_JSON.write_text(
    json.dumps({
        "run_id": RUN_ID,
        "archived_at_utc": RUN_TS.isoformat(),
        "project_root": PROJECT_ROOT.as_posix(),
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
        "counts": {"manifests": n_manifests, "files_total": len(all_files)},
    }, indent=2, ensure_ascii=False),
    encoding="utf-8"
)

# ------------------------------------------------------------
# 8) Build AIP BagIt-like (AIP_bag/data payload)
# ------------------------------------------------------------
if AIP_BAG_DIR.exists():
    shutil.rmtree(AIP_BAG_DIR)
AIP_DATA_DIR.mkdir(parents=True, exist_ok=True)

# payload: kopiere die SIP-Struktur in AIP_bag/data/
payload_items = [
    "00_README.txt",
    "retention_policy.txt",
    "fair_notes.txt",
    "inventory.csv",
    "checksums.sha256",
    "01_manifests",
    "02_reports",
    "03_marts_deidentified",
    "04_ml",
    "05_db_deidentified",
    "06_metadata",
]
for item in payload_items:
    src = ARCHIVE_DIR / item
    dst = AIP_DATA_DIR / item
    if src.is_dir():
        copy_tree_if_exists(src, dst)
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

# BagIt tag files
AIP_BAGIT_TXT.write_text("BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n", encoding="utf-8")
AIP_BAGINFO_TXT.write_text(
    "\n".join([
        "Bag-Software-Agent: Projektarbeit Pipeline Step13",
        f"Bagging-Date: {RUN_TS.date().isoformat()}",
        f"External-Identifier: {RUN_ID}",
        f"Internal-Sender-Identifier: {RUN_ID}-{TS_TAG}",
        "Source-Organization: Projektarbeit",
        "Contact-Name: TODO",
    ]),
    encoding="utf-8"
)

# Fixity for payload: manifest-sha256.txt (relative to bag root)
_ = write_sha256_manifest(root_dir=AIP_DATA_DIR, manifest_path=AIP_MANIFEST, rel_base=AIP_BAG_DIR)

# Verify immediately
fixity_res = verify_sha256_manifest(rel_base=AIP_BAG_DIR, manifest_path=AIP_MANIFEST)
FIXITY_VERIFY.write_text(json.dumps(fixity_res, indent=2, ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------
# 9) Build DIP Access Copy (reduced)
# ------------------------------------------------------------
if DIP_DIR.exists():
    shutil.rmtree(DIP_DIR)
DIP_DIR.mkdir(parents=True, exist_ok=True)

# final
copy_tree_if_exists(OUT_DIR / "final", DIP_DIR / "final", patterns=DIP_FINAL_PATTERNS)

# reports
copy_tree_if_exists(OUT_DIR / "adam_qc", DIP_DIR / "reports" / "adam_qc", patterns=DIP_REPORT_PATTERNS)
copy_tree_if_exists(OUT_DIR / "sdtm_qc", DIP_DIR / "reports" / "sdtm_qc", patterns=DIP_REPORT_PATTERNS)
copy_tree_if_exists(OUT_DIR / "staging_qc", DIP_DIR / "reports" / "staging_qc", patterns=DIP_REPORT_PATTERNS)

# anonymized marts
copy_tree_if_exists(DIR_MARTS, DIP_DIR / "marts_deidentified", patterns=DIP_MART_PATTERNS)

# include README + fixity verification
copy_if_exists(README_TXT, DIP_DIR / "README.txt")
copy_if_exists(FIXITY_VERIFY, DIP_DIR / "fixity_verification.json")

# DIP fixity
_ = write_sha256_manifest(root_dir=DIP_DIR, manifest_path=DIP_MANIFEST, rel_base=DIP_DIR)

# ------------------------------------------------------------
# 10) Export AIP (tar.gz) + optional zip convenience
# ------------------------------------------------------------
aip_targz_path = ARCHIVE_ROOT / f"AIP_{RUN_ID}_{TS_TAG}.tar.gz"
if MAKE_TARGZ:
    with tarfile.open(aip_targz_path, "w:gz") as tar:
        tar.add(AIP_BAG_DIR, arcname=AIP_BAG_DIR.name)

zip_path = ARCHIVE_ROOT / f"archive_{RUN_ID}_{TS_TAG}.zip"
if MAKE_ZIP:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted([q for q in ARCHIVE_DIR.rglob("*") if q.is_file()]):
            z.write(p, arcname=p.relative_to(ARCHIVE_DIR).as_posix())

# ------------------------------------------------------------
# 11) Summary
# ------------------------------------------------------------
print("\n" + "-" * 70)
print("ARCHIVE – SUMMARY")
print("-" * 70)
print(f"[run] run_id                   : {RUN_ID}")
print(f"[root] project_root            : {PROJECT_ROOT}")
print(f"[out] archive_root             : {ARCHIVE_ROOT}")
print(f"[out] archive_dir              : {ARCHIVE_DIR}")
print(f"[aip] bag_dir                  : {AIP_BAG_DIR}")
print(f"[aip] manifest                 : {AIP_MANIFEST}")
print(f"[aip] fixity ok                : {fixity_res.get('ok')} (checked={fixity_res.get('checked')})")
if MAKE_TARGZ:
    print(f"[aip] targz                    : {aip_targz_path}")
print(f"[dip] dip_dir                  : {DIP_DIR}")
print(f"[dip] dip_manifest             : {DIP_MANIFEST}")
if MAKE_ZIP:
    print(f"[out] zip                     : {zip_path}")
print("=" * 70 + "\n")
print("STEP 13 DONE")
