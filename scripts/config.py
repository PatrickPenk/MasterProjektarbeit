from __future__ import annotations
from pathlib import Path

# ============================================================
# 1. Ordner definieren
# ============================================================

try:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
except NameError:
    PROJECT_ROOT = Path.cwd()

DATA_DIR     = PROJECT_ROOT / "data"
RAW_DIR      = DATA_DIR / "raw"
EXTRACT_DIR  = DATA_DIR / "extracted"
DUCKDB_DIR   = DATA_DIR / "duckdb"

OUT_DIR      = PROJECT_ROOT / "out"
ARCHIVE_ROOT = PROJECT_ROOT / "archive"

MANIFEST_DIR = OUT_DIR / "manifests"

# ============================================================
# 2. Unterordner
# ============================================================
RAW_PROF_DIR   = OUT_DIR / "raw_profiling"
STAGING_QC_DIR = OUT_DIR / "staging_qc"
CURATED_DIR = OUT_DIR / "curated"
SDTM_DIR    = OUT_DIR / "sdtm"
SDTM_QC_DIR = OUT_DIR / "sdtm_qc"
ADAM_DIR    = OUT_DIR / "adam"
ADAM_QC_DIR = OUT_DIR / "adam_qc"
MARTS_DIR   = OUT_DIR / "marts"
ML_DIR      = OUT_DIR / "ml"
FINAL_DIR      = OUT_DIR / "final"

# ============================================================
# 3. Datenbank
# ============================================================
DB_PATH      = DUCKDB_DIR / "warehouse.duckdb"

# ============================================================
# 4. Data Contract: Core Tables
# ============================================================
CORE_TABLES = [
    "patients",
    "encounters",
    "conditions",
    "procedures",
    "medications",
    "observations",
]

# ============================================================
# 5. Initialisierung
# ============================================================
def ensure_dirs() -> None:
    for d in [
        DATA_DIR,
        RAW_DIR,
        EXTRACT_DIR,
        DUCKDB_DIR,
        OUT_DIR,
        MANIFEST_DIR,
        RAW_PROF_DIR,
        STAGING_QC_DIR,
        CURATED_DIR,
        SDTM_DIR,
        SDTM_QC_DIR,
        ADAM_DIR,
        ADAM_QC_DIR,
        MARTS_DIR,
        ML_DIR,
        FINAL_DIR,
        ARCHIVE_ROOT,
    ]:
        d.mkdir(parents=True, exist_ok=True)

# ============================================================
# 6. Ausgabe
# ============================================================
if __name__ == "__main__":
    ensure_dirs()
    print("PROJECT_ROOT     =", PROJECT_ROOT)
    print("DATA_DIR         =", DATA_DIR)
    print("RAW_DIR          =", RAW_DIR)
    print("EXTRACT_DIR      =", EXTRACT_DIR)
    print("DUCKDB_DIR       =", DUCKDB_DIR)
    print("OUT_DIR          =", OUT_DIR)
    print("MANIFEST_DIR     =", MANIFEST_DIR)
    print("ARCHIVE_ROOT     =", ARCHIVE_ROOT)
    print("DB_PATH          =", DB_PATH)
