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

# ============================================================
# 2. Outputs / Artefakte
# ============================================================
OUT_DIR      = PROJECT_ROOT / "out"
#DWH_DIR      = PROJECT_ROOT / "dwh"

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
        #DWH_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)

# ============================================================
# 6. Ausgabe
# ============================================================
if __name__ == "__main__":
    ensure_dirs()
    print("PROJECT_ROOT =", PROJECT_ROOT)
    print("DATA_DIR     =", DATA_DIR)
    print("RAW_DIR      =", RAW_DIR)
    print("EXTRACT_DIR  =", EXTRACT_DIR)
    print("DUCKDB_DIR   =", DUCKDB_DIR)
    print("OUT_DIR      =", OUT_DIR)
    #print("ARTIFACT_DIR =", ARTIFACT_DIR)
    print("DB_PATH      =", DB_PATH)
