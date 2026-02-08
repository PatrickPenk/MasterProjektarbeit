# ============================================================
# 05_curated_transform.py
# Curated Layer (+Quarantine + RI/PK checks + HTML/JSON Report)
# ============================================================

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from html import escape

import duckdb

from scripts.config import DB_PATH, OUT_DIR, ensure_dirs

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 05 - CURATED TRANSFORMATIONS (QUARANTINE + RI/PK + REPORT)")
print("=" * 70)

# ============================================================
# 1) Manifest laden (Input wie Step 03/04)
# ============================================================

manifest_candidates = [
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
        + "\nBitte zuerst Step 03 (und idealerweise Step 04) ausführen."
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
# 2) Output Paths (wie andere Steps: deterministische Namen)
# ============================================================

CUR_DIR = OUT_DIR / "curated"
CUR_DIR.mkdir(parents=True, exist_ok=True)

REPORT_HTML_PATH = CUR_DIR / f"dq_curated_report_{RUN_ID}.html"
RESULTS_JSON_PATH = CUR_DIR / f"dq_curated_results_{RUN_ID}.json"

# Manifest Output (analog Step 03/04)
MANIFEST_OUT_PATH = OUT_DIR / "manifest_curated.json"

# ---------------------------
# Helpers
# ---------------------------

def connect_with_retry(db_path: str, retries: int = 20, sleep_s: float = 0.5) -> duckdb.DuckDBPyConnection:
    last_err = None
    for _ in range(retries):
        try:
            return duckdb.connect(db_path)
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)
    raise RuntimeError(
        f"DuckDB ist gesperrt oder nicht erreichbar: {db_path}\nLetzter Fehler: {last_err}"
    ) from last_err


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()[0] > 0


def fetchone_int(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0])


def html_table(rows, headers):
    th = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows
    )
    return (
        "<table border='1' cellspacing='0' cellpadding='6'>"
        f"<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"
    )


con = connect_with_retry(str(DB_PATH))
con.execute("PRAGMA threads=4")

try:
    # ------------------------------------------------------------
    # 0) Preconditions: staging tables must exist
    # ------------------------------------------------------------
    required_stg = [
        "stg_patients",
        "stg_encounters",
        "stg_conditions",
        "stg_observations",
        "stg_medications",
        "stg_procedures",
    ]
    missing = [t for t in required_stg if not table_exists(con, t)]
    if missing:
        raise RuntimeError(
            "Fehlende Staging-Tabellen. Stelle sicher, dass Step 03 alle CSVs geladen hat.\n"
            f"Missing: {missing}"
        )

    # ============================================================
    # 1) CURATED PATIENTS (+rejects)
    # ============================================================
    print("[cur] patients")

    con.execute("""
    CREATE OR REPLACE TABLE rej_patients AS
    SELECT
        *,
        CASE
            WHEN Id IS NULL THEN 'missing_patient_id'
            ELSE NULL
        END AS reject_reason
    FROM stg_patients
    WHERE Id IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_patients AS
    SELECT
        Id                              AS patient_id,
        CAST(BirthDate AS DATE)         AS birth_date,
        CAST(DeathDate AS DATE)         AS death_date,
        Gender                          AS gender,
        Race                            AS race,
        Ethnicity                        AS ethnicity,
        City                            AS city,
        State                           AS state,
        Zip                             AS zip,
        CAST(Income AS DOUBLE)          AS income,
        CAST(Healthcare_Expenses AS DOUBLE) AS lifetime_expenses,
        CAST(Healthcare_Coverage AS DOUBLE) AS lifetime_coverage,
        _run_id,
        _ingested_at
    FROM stg_patients
    WHERE Id IS NOT NULL
    """)

    # ============================================================
    # 2) CURATED ENCOUNTERS (+rejects, cast-safe comparisons)
    # ============================================================
    print("[cur] encounters")

    con.execute("""
    CREATE OR REPLACE TABLE rej_encounters AS
    WITH base AS (
      SELECT
        *,
        CAST(Start AS TIMESTAMP) AS start_ts_cast,
        CAST(Stop  AS TIMESTAMP) AS stop_ts_cast,
        CAST(Base_Encounter_Cost AS DOUBLE) AS base_cost_cast,
        CAST(Total_Claim_Cost    AS DOUBLE) AS total_cost_cast
      FROM stg_encounters
    )
    SELECT
      * EXCLUDE (start_ts_cast, stop_ts_cast, base_cost_cast, total_cost_cast),
      CASE
        WHEN Id IS NULL THEN 'missing_encounter_id'
        WHEN Patient IS NULL THEN 'missing_patient_id'
        WHEN stop_ts_cast IS NOT NULL AND start_ts_cast IS NOT NULL AND start_ts_cast > stop_ts_cast
          THEN 'bad_time_order_start_gt_stop'
        WHEN base_cost_cast IS NOT NULL AND base_cost_cast < 0
          THEN 'negative_base_cost'
        WHEN total_cost_cast IS NOT NULL AND total_cost_cast < 0
          THEN 'negative_total_cost'
        ELSE NULL
      END AS reject_reason
    FROM base
    WHERE
      Id IS NULL
      OR Patient IS NULL
      OR (stop_ts_cast IS NOT NULL AND start_ts_cast IS NOT NULL AND start_ts_cast > stop_ts_cast)
      OR (base_cost_cast IS NOT NULL AND base_cost_cast < 0)
      OR (total_cost_cast IS NOT NULL AND total_cost_cast < 0)
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_encounters AS
    WITH base AS (
      SELECT
        *,
        CAST(Start AS TIMESTAMP) AS start_ts_cast,
        CAST(Stop  AS TIMESTAMP) AS stop_ts_cast,
        CAST(Base_Encounter_Cost AS DOUBLE) AS base_cost_cast,
        CAST(Total_Claim_Cost    AS DOUBLE) AS total_cost_cast,
        CAST(Payer_Coverage      AS DOUBLE) AS payer_coverage_cast
      FROM stg_encounters
    )
    SELECT
        Id                         AS encounter_id,
        Patient                    AS patient_id,
        Organization               AS organization_id,
        Provider                   AS provider_id,
        Payer                      AS payer_id,

        start_ts_cast              AS start_ts,
        stop_ts_cast               AS stop_ts,

        EncounterClass             AS encounter_class,
        Code                       AS encounter_code,
        Description                AS encounter_desc,

        base_cost_cast             AS base_cost,
        total_cost_cast            AS total_cost,
        payer_coverage_cast        AS payer_coverage,

        CASE
            WHEN stop_ts_cast IS NOT NULL AND start_ts_cast IS NOT NULL AND start_ts_cast <= stop_ts_cast
            THEN DATE_DIFF('day', CAST(start_ts_cast AS DATE), CAST(stop_ts_cast AS DATE))
            ELSE NULL
        END AS los_days,

        _run_id,
        _ingested_at

    FROM base
    WHERE Id IS NOT NULL
      AND Patient IS NOT NULL
      AND (start_ts_cast IS NULL OR stop_ts_cast IS NULL OR start_ts_cast <= stop_ts_cast)
      AND (base_cost_cast IS NULL OR base_cost_cast >= 0)
      AND (total_cost_cast IS NULL OR total_cost_cast >= 0)
    """)

    # ============================================================
    # 3) CURATED CONDITIONS (+rejects)
    # ============================================================
    print("[cur] conditions")

    con.execute("""
    CREATE OR REPLACE TABLE rej_conditions AS
    WITH base AS (
      SELECT
        *,
        CAST(Start AS DATE) AS start_date_cast,
        CAST(Stop  AS DATE) AS stop_date_cast
      FROM stg_conditions
    )
    SELECT
      * EXCLUDE (start_date_cast, stop_date_cast),
      CASE
        WHEN Patient IS NULL THEN 'missing_patient_id'
        WHEN Encounter IS NULL THEN 'missing_encounter_id'
        WHEN stop_date_cast IS NOT NULL AND start_date_cast IS NOT NULL AND start_date_cast > stop_date_cast
          THEN 'bad_time_order_start_gt_stop'
        ELSE NULL
      END AS reject_reason
    FROM base
    WHERE
      Patient IS NULL
      OR Encounter IS NULL
      OR (stop_date_cast IS NOT NULL AND start_date_cast IS NOT NULL AND start_date_cast > stop_date_cast)
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_conditions AS
    WITH base AS (
      SELECT
        *,
        CAST(Start AS DATE) AS start_date_cast,
        CAST(Stop  AS DATE) AS stop_date_cast
      FROM stg_conditions
    )
    SELECT
        Patient                    AS patient_id,
        Encounter                  AS encounter_id,
        start_date_cast            AS start_date,
        stop_date_cast             AS stop_date,
        Code                       AS condition_code,
        Description                AS condition_desc,
        _run_id,
        _ingested_at
    FROM base
    WHERE Patient IS NOT NULL
      AND Encounter IS NOT NULL
      AND (start_date_cast IS NULL OR stop_date_cast IS NULL OR start_date_cast <= stop_date_cast)
    """)

    # ============================================================
    # 3b) CURATED OBSERVATIONS (+rejects)
    # ============================================================
    print("[cur] observations")

    con.execute("""
    CREATE OR REPLACE TABLE rej_observations AS
    WITH base AS (
      SELECT
        *,
        CAST(DATE AS TIMESTAMP) AS obs_ts_cast
      FROM stg_observations
    )
    SELECT
      * EXCLUDE (obs_ts_cast),
      CASE
        WHEN Patient IS NULL THEN 'missing_patient_id'
        WHEN Code IS NULL THEN 'missing_code'
        WHEN obs_ts_cast IS NULL AND DATE IS NOT NULL THEN 'bad_date_parse'
        ELSE NULL
      END AS reject_reason
    FROM base
    WHERE
      Patient IS NULL
      OR Code IS NULL
      OR (obs_ts_cast IS NULL AND DATE IS NOT NULL)
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_observations AS
    WITH base AS (
      SELECT
        *,
        CAST(DATE AS TIMESTAMP) AS obs_ts_cast
      FROM stg_observations
    )
    SELECT
      Patient                 AS patient_id,
      Encounter               AS encounter_id,
      Code                    AS code,
      Description             AS description,
      Value                   AS value,
      Units                   AS units,
      obs_ts_cast             AS obs_date,
      _run_id,
      _ingested_at
    FROM base
    WHERE Patient IS NOT NULL
      AND Code IS NOT NULL
    """)

    # ============================================================
    # 3c) CURATED MEDICATIONS (+rejects)
    # ============================================================
    print("[cur] medications")

    con.execute("""
    CREATE OR REPLACE TABLE rej_medications AS
    WITH base AS (
      SELECT
        *,
        CAST(Start AS DATE) AS start_date_cast,
        CAST(Stop  AS DATE) AS stop_date_cast
      FROM stg_medications
    )
    SELECT
      * EXCLUDE (start_date_cast, stop_date_cast),
      CASE
        WHEN Patient IS NULL THEN 'missing_patient_id'
        WHEN Code IS NULL AND Description IS NULL THEN 'missing_code_and_description'
        WHEN stop_date_cast IS NOT NULL AND start_date_cast IS NOT NULL AND start_date_cast > stop_date_cast
          THEN 'bad_time_order_start_gt_stop'
        ELSE NULL
      END AS reject_reason
    FROM base
    WHERE
      Patient IS NULL
      OR (Code IS NULL AND Description IS NULL)
      OR (stop_date_cast IS NOT NULL AND start_date_cast IS NOT NULL AND start_date_cast > stop_date_cast)
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_medications AS
    WITH base AS (
      SELECT
        *,
        CAST(Start AS DATE) AS start_date_cast,
        CAST(Stop  AS DATE) AS stop_date_cast
      FROM stg_medications
    )
    SELECT
      Patient                 AS patient_id,
      Encounter               AS encounter_id,
      Code                    AS med_code,
      Description             AS med_desc,
      start_date_cast         AS start_date,
      stop_date_cast          AS stop_date,
      _run_id,
      _ingested_at
    FROM base
    WHERE Patient IS NOT NULL
      AND (Code IS NOT NULL OR Description IS NOT NULL)
      AND (start_date_cast IS NULL OR stop_date_cast IS NULL OR start_date_cast <= stop_date_cast)
    """)

    # ============================================================
    # 3d) CURATED PROCEDURES (+rejects) – schema-robust
    # ============================================================
    print("[cur] procedures")

    cols = set(r[0].upper() for r in con.execute("DESCRIBE stg_procedures").fetchall())

    date_candidates = ["START", "DATE", "PERFORMEDDATE", "PERFORMED_DATE", "PERFORMED", "PROCEDUREDATE", "PROCEDURE_DATE"]
    end_candidates  = ["STOP", "END", "ENDDATE", "END_DATE"]

    start_col = next((c for c in date_candidates if c in cols), None)
    end_col   = next((c for c in end_candidates if c in cols), None)

    if start_col is None:
        raise RuntimeError(
            "stg_procedures: Keine Datums-Spalte gefunden. "
            f"Vorhandene Spalten: {sorted(cols)}"
        )

    start_expr = f"CAST({start_col} AS DATE)"
    end_expr = f"CAST({end_col} AS DATE)" if end_col else "NULL::DATE"

    con.execute(f"""
    CREATE OR REPLACE TABLE rej_procedures AS
    WITH base AS (
    SELECT
        *,
        {start_expr} AS start_date_cast,
        {end_expr}   AS stop_date_cast
    FROM stg_procedures
    )
    SELECT
    * EXCLUDE (start_date_cast, stop_date_cast),
    CASE
        WHEN Patient IS NULL THEN 'missing_patient_id'
        WHEN (Code IS NULL AND Description IS NULL) THEN 'missing_code_and_description'
        WHEN start_date_cast IS NULL THEN 'missing_date'
        WHEN stop_date_cast IS NOT NULL AND start_date_cast IS NOT NULL AND start_date_cast > stop_date_cast
        THEN 'bad_time_order_start_gt_stop'
        ELSE NULL
    END AS reject_reason
    FROM base
    WHERE
    Patient IS NULL
    OR (Code IS NULL AND Description IS NULL)
    OR start_date_cast IS NULL
    OR (stop_date_cast IS NOT NULL AND start_date_cast IS NOT NULL AND start_date_cast > stop_date_cast)
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE cur_procedures AS
    WITH base AS (
    SELECT
        *,
        {start_expr} AS start_date_cast,
        {end_expr}   AS stop_date_cast
    FROM stg_procedures
    )
    SELECT
    Patient                 AS patient_id,
    Encounter               AS encounter_id,
    Code                    AS proc_code,
    Description             AS proc_desc,
    start_date_cast         AS start_date,
    stop_date_cast          AS stop_date,
    _run_id,
    _ingested_at
    FROM base
    WHERE Patient IS NOT NULL
    AND (Code IS NOT NULL OR Description IS NOT NULL)
    AND start_date_cast IS NOT NULL
    AND (stop_date_cast IS NULL OR start_date_cast <= stop_date_cast)
    """)

    # ============================================================
    # 4) Referential Integrity (RI): rejects + RI-clean tables
    # ============================================================
    print("[cur] RI checks")

    con.execute("""
    CREATE OR REPLACE TABLE rej_encounters_ri AS
    SELECT
      e.*,
      'encounter_patient_fk_missing' AS reject_reason
    FROM cur_encounters e
    LEFT JOIN cur_patients p ON e.patient_id = p.patient_id
    WHERE p.patient_id IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE rej_conditions_ri AS
    SELECT
      c.*,
      CASE
        WHEN p.patient_id IS NULL THEN 'condition_patient_fk_missing'
        WHEN e.encounter_id IS NULL THEN 'condition_encounter_fk_missing'
        ELSE 'ri_unknown'
      END AS reject_reason
    FROM cur_conditions c
    LEFT JOIN cur_patients p ON c.patient_id = p.patient_id
    LEFT JOIN cur_encounters e ON c.encounter_id = e.encounter_id
    WHERE p.patient_id IS NULL OR e.encounter_id IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE rej_observations_ri AS
    SELECT
      o.*,
      'observation_patient_fk_missing' AS reject_reason
    FROM cur_observations o
    LEFT JOIN cur_patients p ON o.patient_id = p.patient_id
    WHERE p.patient_id IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE rej_medications_ri AS
    SELECT
      m.*,
      'medication_patient_fk_missing' AS reject_reason
    FROM cur_medications m
    LEFT JOIN cur_patients p ON m.patient_id = p.patient_id
    WHERE p.patient_id IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE rej_procedures_ri AS
    SELECT
      pr.*,
      'procedure_patient_fk_missing' AS reject_reason
    FROM cur_procedures pr
    LEFT JOIN cur_patients p ON pr.patient_id = p.patient_id
    WHERE p.patient_id IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_encounters_ri AS
    SELECT e.*
    FROM cur_encounters e
    LEFT JOIN rej_encounters_ri r ON e.encounter_id = r.encounter_id
    WHERE r.encounter_id IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_conditions_ri AS
    SELECT c.*
    FROM cur_conditions c
    LEFT JOIN rej_conditions_ri r
      ON c.patient_id = r.patient_id
     AND c.encounter_id = r.encounter_id
     AND COALESCE(c.condition_code,'') = COALESCE(r.condition_code,'')
     AND (c.start_date = r.start_date OR (c.start_date IS NULL AND r.start_date IS NULL))
    WHERE r.reject_reason IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_observations_ri AS
    SELECT o.*
    FROM cur_observations o
    LEFT JOIN rej_observations_ri r
      ON o.patient_id = r.patient_id
     AND COALESCE(o.code,'') = COALESCE(r.code,'')
     AND (o.obs_date = r.obs_date OR (o.obs_date IS NULL AND r.obs_date IS NULL))
    WHERE r.reject_reason IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_medications_ri AS
    SELECT m.*
    FROM cur_medications m
    LEFT JOIN rej_medications_ri r
      ON m.patient_id = r.patient_id
     AND COALESCE(m.med_code,'') = COALESCE(r.med_code,'')
     AND (m.start_date = r.start_date OR (m.start_date IS NULL AND r.start_date IS NULL))
    WHERE r.reject_reason IS NULL
    """)

    con.execute("""
    CREATE OR REPLACE TABLE cur_procedures_ri AS
    SELECT pr.*
    FROM cur_procedures pr
    LEFT JOIN rej_procedures_ri r
      ON pr.patient_id = r.patient_id
     AND COALESCE(pr.proc_code,'') = COALESCE(r.proc_code,'')
     AND (pr.start_date = r.start_date OR (pr.start_date IS NULL AND r.start_date IS NULL))
    WHERE r.reject_reason IS NULL
    """)

    # ============================================================
    # 5) Duplicate / PK diagnostics
    # ============================================================
    print("[cur] duplicate checks")

    dup_patient_groups = fetchone_int(con, """
    SELECT COUNT(*) FROM (
      SELECT patient_id
      FROM cur_patients
      GROUP BY patient_id
      HAVING COUNT(*) > 1
    ) x
    """)

    dup_encounter_groups = fetchone_int(con, """
    SELECT COUNT(*) FROM (
      SELECT encounter_id
      FROM cur_encounters
      GROUP BY encounter_id
      HAVING COUNT(*) > 1
    ) x
    """)

    dup_conditions_groups = fetchone_int(con, """
    SELECT COUNT(*) FROM (
      SELECT patient_id, encounter_id, condition_code, start_date
      FROM cur_conditions
      GROUP BY 1,2,3,4
      HAVING COUNT(*) > 1
    ) x
    """)

    # ============================================================
    # 6) Counts + checks
    # ============================================================
    print("\n[cur] counts")

    counts = {
        "cur_patients": fetchone_int(con, "SELECT COUNT(*) FROM cur_patients"),
        "cur_encounters": fetchone_int(con, "SELECT COUNT(*) FROM cur_encounters"),
        "cur_conditions": fetchone_int(con, "SELECT COUNT(*) FROM cur_conditions"),
        "cur_observations": fetchone_int(con, "SELECT COUNT(*) FROM cur_observations"),
        "cur_medications": fetchone_int(con, "SELECT COUNT(*) FROM cur_medications"),
        "cur_procedures": fetchone_int(con, "SELECT COUNT(*) FROM cur_procedures"),

        "cur_encounters_ri": fetchone_int(con, "SELECT COUNT(*) FROM cur_encounters_ri"),
        "cur_conditions_ri": fetchone_int(con, "SELECT COUNT(*) FROM cur_conditions_ri"),
        "cur_observations_ri": fetchone_int(con, "SELECT COUNT(*) FROM cur_observations_ri"),
        "cur_medications_ri": fetchone_int(con, "SELECT COUNT(*) FROM cur_medications_ri"),
        "cur_procedures_ri": fetchone_int(con, "SELECT COUNT(*) FROM cur_procedures_ri"),

        "rej_patients": fetchone_int(con, "SELECT COUNT(*) FROM rej_patients"),
        "rej_encounters": fetchone_int(con, "SELECT COUNT(*) FROM rej_encounters"),
        "rej_conditions": fetchone_int(con, "SELECT COUNT(*) FROM rej_conditions"),
        "rej_observations": fetchone_int(con, "SELECT COUNT(*) FROM rej_observations"),
        "rej_medications": fetchone_int(con, "SELECT COUNT(*) FROM rej_medications"),
        "rej_procedures": fetchone_int(con, "SELECT COUNT(*) FROM rej_procedures"),

        "rej_encounters_ri": fetchone_int(con, "SELECT COUNT(*) FROM rej_encounters_ri"),
        "rej_conditions_ri": fetchone_int(con, "SELECT COUNT(*) FROM rej_conditions_ri"),
        "rej_observations_ri": fetchone_int(con, "SELECT COUNT(*) FROM rej_observations_ri"),
        "rej_medications_ri": fetchone_int(con, "SELECT COUNT(*) FROM rej_medications_ri"),
        "rej_procedures_ri": fetchone_int(con, "SELECT COUNT(*) FROM rej_procedures_ri"),
    }

    checks = {
        "pk_dup_patients_n_groups": dup_patient_groups,
        "pk_dup_encounters_n_groups": dup_encounter_groups,
        "dup_conditions_heuristic_n_groups": dup_conditions_groups,
        "ri_missing_encounters_patient_rows": counts["rej_encounters_ri"],
        "ri_missing_conditions_rows": counts["rej_conditions_ri"],
        "ri_missing_observations_patient_rows": counts["rej_observations_ri"],
        "ri_missing_medications_patient_rows": counts["rej_medications_ri"],
        "ri_missing_procedures_patient_rows": counts["rej_procedures_ri"],
    }

    for k, v in counts.items():
        print(f"[cur] {k:28s}: {v}")
    for k, v in checks.items():
        print(f"[dq ] {k:38s}: {v}")

    # ============================================================
    # 7) Persist results + HTML report
    # ============================================================
    payload = {
        "run_id": RUN_ID,
        "run_ts_utc": RUN_TS.isoformat(),
        "db_path": str(DB_PATH),
        "counts": counts,
        "checks": checks,
        "recommended_downstream_tables": [
            "cur_patients",
            "cur_encounters_ri",
            "cur_conditions_ri",
            "cur_observations_ri",
            "cur_medications_ri",
            "cur_procedures_ri",
        ],
    }
    RESULTS_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    sections = []
    sections.append("<h1>STEP 05 - Curated DQ Report</h1>")
    sections.append(
        f"<p><b>run_id</b>: {escape(RUN_ID)}<br>"
        f"<b>run_ts_utc</b>: {escape(RUN_TS.isoformat())}</p>"
    )

    sections.append("<h2>Row counts</h2>")
    sections.append(html_table(list(counts.items()), headers=["metric", "value"]))

    sections.append("<h2>DQ checks</h2>")
    sections.append(html_table(list(checks.items()), headers=["check", "value"]))

    sections.append("<h2>Downstream recommendation</h2>")
    sections.append("<ul>")
    for t in payload["recommended_downstream_tables"]:
        sections.append(f"<li><b>{escape(t)}</b></li>")
    sections.append("</ul>")

    html = (
        "<html><head><meta charset='utf-8'>"
        "<title>Curated DQ Report</title></head><body>"
        + "\n".join(sections)
        + "</body></html>"
    )
    REPORT_HTML_PATH.write_text(html, encoding="utf-8")

    # ============================================================
    # 8) Manifest updaten (wie Step 03/04)
    # ============================================================
    manifest_out = dict(manifest_in)
    manifest_out.update({
        "curated": {
            "built_at_utc": RUN_TS.isoformat(),
            "db_path": DB_PATH.as_posix(),
            "output_dir": CUR_DIR.as_posix(),
            "dq_results_json": RESULTS_JSON_PATH.as_posix(),
            "dq_report_html": REPORT_HTML_PATH.as_posix(),
            "required_staging_tables": required_stg,
            "tables": {
                "curated": [
                    "cur_patients",
                    "cur_encounters",
                    "cur_conditions",
                    "cur_observations",
                    "cur_medications",
                    "cur_procedures",
                ],
                "rejects": [
                    "rej_patients",
                    "rej_encounters",
                    "rej_conditions",
                    "rej_observations",
                    "rej_medications",
                    "rej_procedures",
                ],
                "ri_rejects": [
                    "rej_encounters_ri",
                    "rej_conditions_ri",
                    "rej_observations_ri",
                    "rej_medications_ri",
                    "rej_procedures_ri",
                ],
                "ri_clean": [
                    "cur_encounters_ri",
                    "cur_conditions_ri",
                    "cur_observations_ri",
                    "cur_medications_ri",
                    "cur_procedures_ri",
                ],
            },
            "counts": counts,
            "checks": checks,
            "recommended_downstream_tables": payload["recommended_downstream_tables"],
        }
    })

    MANIFEST_OUT_PATH.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

    print("\n[ok] wrote:", RESULTS_JSON_PATH)
    print("[ok] wrote:", REPORT_HTML_PATH)
    print("[ok] wrote:", MANIFEST_OUT_PATH)
    print("\nSTEP 05 DONE")

finally:
    try:
        con.close()
    except Exception:
        pass
