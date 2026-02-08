# ============================================================
# 06_sdtm_mapping.py
# SDTM Mapping (Curated → SDTM Domains)
# Covers: DM, SV, LB, VS, MH, CM, PR
# ============================================================

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Dict

import duckdb

from scripts.config import DB_PATH, OUT_DIR, ensure_dirs

ensure_dirs()

print("\n" + "=" * 60)
print("STEP 06 – SDTM MAPPING (CURATED → SDTM)")
print("=" * 60)

# ============================================================
# 1) Manifest laden (Input wie Step 03/04/05)
# ============================================================

manifest_candidates = [
    OUT_DIR / "manifest_curated.json",
    OUT_DIR / "manifest_staging_checked.json",
    OUT_DIR / "manifest_staging.json",
    OUT_DIR / "manifest_raw_profiled.json",
    OUT_DIR / "manifest_raw.json",
]
manifest_in_path = next((p for p in manifest_candidates if p.exists()), None)
if manifest_in_path is None:
    raise FileNotFoundError(
        "Kein Manifest gefunden. Erwartet (in dieser Reihenfolge):\n"
        + "\n".join([f" - {p}" for p in manifest_candidates])
        + "\nBitte zuerst Step 05 (Curated) ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
run_id = manifest_in.get("run_id")
if not run_id:
    raise ValueError("run_id fehlt im Manifest. Bitte Step 03 erneut ausführen.")

RUN_ID = run_id
RUN_TS = datetime.now(timezone.utc)

print(f"[in ] manifest            : {manifest_in_path}")
print(f"[run] run_id              : {RUN_ID}")
print(f"[db ] path                : {DB_PATH}")

# ============================================================
# 2) Config / Constants
# ============================================================

STUDYID = "SYNTH-01"
LAYER = "sdtm"

SDTM_DIR = OUT_DIR / "sdtm"
SDTM_DIR.mkdir(parents=True, exist_ok=True)

MANIFEST_OUT_PATH = OUT_DIR / "manifest_sdtm.json"

# VS split config: explicit and auditable
# You can extend this list as needed.
VS_LOINC: Dict[str, str] = {
    "BP_DIA": "8462-4",   # Diastolic blood pressure
    "BP_SYS": "8480-6",   # Systolic blood pressure
    "HR":     "8867-4",   # Heart rate
    "TEMP":   "8310-5",   # Body temperature
    # Common extensions (optional):
    # "RR":   "9279-1",   # Respiratory rate
    # "WT":   "29463-7",  # Body weight
    # "HT":   "8302-2",   # Body height
    # "BMI":  "39156-5",  # BMI
}
VS_LOINC_LIST_SQL = ",".join([f"'{v}'" for v in VS_LOINC.values()])

# ============================================================
# 3) Helpers
# ============================================================

def connect_with_retry(db_path: str, retries: int = 20, sleep_s: float = 0.5) -> duckdb.DuckDBPyConnection:
    last_err = None
    for _ in range(retries):
        try:
            return duckdb.connect(db_path)
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)
    raise RuntimeError(f"DuckDB ist gesperrt: {db_path}\nLetzter Fehler: {last_err}") from last_err

def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()[0] > 0

def fetchone_int(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0])

con = connect_with_retry(str(DB_PATH))
con.execute("PRAGMA threads=4")

try:
    # ------------------------------------------------------------
    # Preconditions: curated RI-clean inputs
    # ------------------------------------------------------------
    required_cur = [
        "cur_patients",
        "cur_encounters_ri",
        "cur_conditions_ri",
        "cur_observations_ri",
        "cur_medications_ri",
        "cur_procedures_ri",
    ]
    missing = [t for t in required_cur if not table_exists(con, t)]
    if missing:
        raise RuntimeError(
            "Fehlende Curated-Eingabetabellen. Bitte Step 05 ausführen.\n"
            f"Missing: {missing}"
        )

    # ============================================================
    # DM – Demographics
    # ============================================================
    print("[map] DM")

    con.execute(f"""
    CREATE OR REPLACE TABLE sdtm_dm AS
    SELECT
        '{STUDYID}'                     AS STUDYID,
        'DM'                            AS DOMAIN,
        'SYN-' || patient_id            AS USUBJID,
        CASE
            WHEN UPPER(gender) IN ('M','MALE') THEN 'M'
            WHEN UPPER(gender) IN ('F','FEMALE') THEN 'F'
            ELSE 'U'
        END                             AS SEX,
        CAST(birth_date AS VARCHAR)     AS BRTHDTC,
        CAST(death_date AS VARCHAR)     AS DTHDTC,
        race                            AS RACE,
        ethnicity                       AS ETHNIC
    FROM cur_patients
    """)

    # ============================================================
    # SV – Subject Visits
    # ============================================================
    print("[map] SV")

    con.execute(f"""
    CREATE OR REPLACE TABLE sdtm_sv AS
    SELECT
        '{STUDYID}'                     AS STUDYID,
        'SV'                            AS DOMAIN,
        'SYN-' || patient_id            AS USUBJID,
        ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY start_ts) AS VISITNUM,
        encounter_class                 AS VISIT,
        CAST(start_ts AS VARCHAR)       AS SVSTDTC,
        CAST(stop_ts  AS VARCHAR)       AS SVENDTC
    FROM cur_encounters_ri
    """)

    # ============================================================
    # MH – Medical History
    # ============================================================
    print("[map] MH")

    con.execute(f"""
    CREATE OR REPLACE TABLE sdtm_mh AS
    SELECT
        '{STUDYID}'                     AS STUDYID,
        'MH'                            AS DOMAIN,
        'SYN-' || patient_id            AS USUBJID,
        condition_desc                  AS MHTERM,
        CAST(start_date AS VARCHAR)     AS MHSTDTC,
        CAST(stop_date  AS VARCHAR)     AS MHENDTC
    FROM cur_conditions_ri
    """)

    # ============================================================
    # LB – Laboratory Tests
    # (All observations that are NOT classified as VS by LOINC list)
    # ============================================================
    print("[map] LB")

    con.execute(f"""
    CREATE OR REPLACE TABLE sdtm_lb AS
    SELECT
        '{STUDYID}'                     AS STUDYID,
        'LB'                            AS DOMAIN,
        'SYN-' || patient_id            AS USUBJID,
        code                            AS LBTESTCD,
        description                     AS LBTEST,
        CAST(obs_date AS VARCHAR)       AS LBDTC,
        value                           AS LBORRES,
        units                           AS LBORRESU,
        TRY_CAST(value AS DOUBLE)       AS LBSTRESN,
        units                           AS LBSTRESU
    FROM cur_observations_ri
    WHERE code NOT IN ({VS_LOINC_LIST_SQL})
    """)

    # ============================================================
    # VS – Vital Signs
    # (Split rule: explicit set of LOINC codes)
    # ============================================================
    print("[map] VS")

    con.execute(f"""
    CREATE OR REPLACE TABLE sdtm_vs AS
    SELECT
        '{STUDYID}'                     AS STUDYID,
        'VS'                            AS DOMAIN,
        'SYN-' || patient_id            AS USUBJID,
        code                            AS VSTESTCD,
        description                     AS VSTEST,
        CAST(obs_date AS VARCHAR)       AS VSDTC,
        value                           AS VSORRES,
        units                           AS VSORRESU,
        TRY_CAST(value AS DOUBLE)       AS VSSTRESN,
        units                           AS VSSTRESU
    FROM cur_observations_ri
    WHERE code IN ({VS_LOINC_LIST_SQL})
    """)

    # ============================================================
    # CM – Concomitant Medications
    # ============================================================
    print("[map] CM")

    con.execute(f"""
    CREATE OR REPLACE TABLE sdtm_cm AS
    SELECT
        '{STUDYID}'                     AS STUDYID,
        'CM'                            AS DOMAIN,
        'SYN-' || patient_id            AS USUBJID,
        COALESCE(med_desc, CAST(med_code AS VARCHAR)) AS CMTRT,
        CAST(start_date AS VARCHAR)     AS CMSTDTC,
        CAST(stop_date  AS VARCHAR)     AS CMENDTC
    FROM cur_medications_ri
    """)

    # ============================================================
    # PR – Procedures
    # ============================================================
    print("[map] PR")

    con.execute(f"""
    CREATE OR REPLACE TABLE sdtm_pr AS
    SELECT
        '{STUDYID}'                     AS STUDYID,
        'PR'                            AS DOMAIN,
        'SYN-' || patient_id            AS USUBJID,
        COALESCE(proc_desc, CAST(proc_code AS VARCHAR)) AS PRTRT,
        CAST(start_date AS VARCHAR)     AS PRSTDTC,
        CAST(stop_date  AS VARCHAR)     AS PRENDTC
    FROM cur_procedures_ri
    """)

    # ============================================================
    # Rowcounts (für Manifest)
    # ============================================================
    domain_tables = {
        "DM": "sdtm_dm",
        "SV": "sdtm_sv",
        "MH": "sdtm_mh",
        "LB": "sdtm_lb",
        "VS": "sdtm_vs",
        "CM": "sdtm_cm",
        "PR": "sdtm_pr",
    }
    rowcounts = {dom: fetchone_int(con, f"SELECT COUNT(*) FROM {tbl}") for dom, tbl in domain_tables.items()}

    print("\nSTEP 06 DONE – SDTM domains created:", ", ".join(domain_tables.keys()))
    for dom, n in rowcounts.items():
        print(f"[sdtm] {dom:2s}: {n}")

    # ============================================================
    # Manifest updaten (wie Step 03/04/05)
    # ============================================================
    manifest_out = dict(manifest_in)
    manifest_out.update({
        "sdtm": {
            "layer": LAYER,
            "built_at_utc": RUN_TS.isoformat(),
            "studyid": STUDYID,
            "db_path": DB_PATH.as_posix(),
            "output_dir": SDTM_DIR.as_posix(),
            "required_curated_tables": required_cur,
            "domains": domain_tables,          # <- sauber getrennt
            "rowcounts": rowcounts,            # <- pro Domain
            "vs_split": {
                "rule": "LOINC code IN vs_loinc_codes => VS else LB",
                "vs_loinc_codes": VS_LOINC,    # <- auditierbar
            },
        }
    })

    MANIFEST_OUT_PATH.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")
    print(f"[out] manifest            : {MANIFEST_OUT_PATH}")

finally:
    try:
        con.close()
    except Exception:
        pass
