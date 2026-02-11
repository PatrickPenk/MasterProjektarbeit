# ============================================================
# 04_staging_qc.py
# ============================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb
import pandas as pd
from html import escape

from scripts.config import DB_PATH, MANIFEST_DIR, STAGING_QC_DIR, ensure_dirs, CORE_TABLES

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 04 - STAGING DQ CHECKS (FULL: TECH + RI + PLAUS + DUPLICATES)")
print("=" * 70)

# ============================================================
# 1) Manifest laden (Input aus Step 03)
# ============================================================

manifest_candidates = [
    MANIFEST_DIR / "manifest_staging.json",
    MANIFEST_DIR / "manifest_raw_profiled.json",
    MANIFEST_DIR / "manifest_raw.json",
]

manifest_in_path = next((p for p in manifest_candidates if p.exists()), None)
if manifest_in_path is None:
    raise FileNotFoundError(
        "Kein Manifest gefunden. Erwartet:\n"
        f" - {manifest_candidates[0]}\n"
        f" - {manifest_candidates[1]}\n"
        f" - {manifest_candidates[2]}\n"
        "Bitte zuerst Step 03 ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
run_id = manifest_in.get("run_id")
if not run_id:
    raise ValueError("run_id fehlt im Manifest. Bitte Step 03 erneut ausführen.")

print(f"[in ] manifest            : {manifest_in_path}")
print(f"[run] run_id              : {run_id}")
print(f"[db ] path                : {DB_PATH}")

# ============================================================
# 2) Helpers
# ============================================================

LAYER = "staging"
CHECKED_AT = datetime.now(timezone.utc)


# STRICT_DQ: stoppt nur bei HARD fails
# Additional checks (RI/Plaus/Dups) sind standardmäßig SOFT.
STRICT_DQ = True

TECH_REQUIRED_COLS = ["_run_id", "_ingested_at", "_source_file"]

def open_duckdb(db_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path))
    con.execute("PRAGMA threads=4")
    return con

def ensure_meta_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS meta_dq_results (
        run_id VARCHAR,
        checked_at TIMESTAMP,
        layer VARCHAR,
        check_name VARCHAR,
        table_name VARCHAR,
        severity VARCHAR,        -- HARD | SOFT
        n_bad BIGINT,
        status VARCHAR,          -- PASS | FAIL | SKIP | ERROR
        message VARCHAR
    );
    """)

#duckdb
def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ? LIMIT 1",
        [table],
    ).fetchone() is not None

def list_stg_tables(con: duckdb.DuckDBPyConnection) -> List[str]:
    rows = con.execute("""
        SELECT table_name
        FROM duckdb_tables()
        WHERE table_name LIKE 'stg_%'
        ORDER BY table_name
    """).fetchall()
    return [r[0] for r in rows]

def columns_map_lower(con: duckdb.DuckDBPyConnection, table: str) -> Dict[str, str]:
    """Map lower(col) -> real col name."""
    rows = con.execute(f"DESCRIBE {table}").fetchall()
    return {str(r[0]).lower(): str(r[0]) for r in rows}

def pick_col(cols_lc: Dict[str, str], *candidates: str) -> Optional[str]:
    """Return real col name if any candidate (case-insensitive) exists."""
    for c in candidates:
        if c.lower() in cols_lc:
            return cols_lc[c.lower()]
    return None

def scalar_int(con: duckdb.DuckDBPyConnection, sql: str, params: Optional[List] = None) -> int:
    res = con.execute(sql, params or []).fetchone()
    return int(res[0]) if res and res[0] is not None else 0

def insert_dq(
    con: duckdb.DuckDBPyConnection,
    *,
    check_name: str,
    table_name: str,
    severity: str,
    n_bad: Optional[int],
    status: str,
    message: Optional[str],
) -> None:
    con.execute(
        "INSERT INTO meta_dq_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            CHECKED_AT.replace(tzinfo=None),
            LAYER,
            check_name,
            table_name,
            severity,
            None if n_bad is None else int(n_bad),
            status,
            message,
        ],
    )
    print(
        f"[dq][{status}] {check_name:<28} : {table_name}"
        + (f" (n_bad={n_bad})" if n_bad is not None else "")
        + (f" -> {message}" if message else "")
    )

def log_pass(con, check_name, table_name, severity="SOFT"):
    insert_dq(con, check_name=check_name, table_name=table_name,
              severity=severity, n_bad=0, status="PASS", message=None)

def log_skip(con, check_name, table_name, severity="SOFT", message="not applicable"):
    insert_dq(con, check_name=check_name, table_name=table_name,
              severity=severity, n_bad=None, status="SKIP", message=message)

def log_fail(con, check_name, table_name, severity, n_bad, message):
    insert_dq(con, check_name=check_name, table_name=table_name,
              severity=severity, n_bad=n_bad, status="FAIL", message=message)

# ============================================================
# 3) Welche Tabellen prüfen
# ============================================================

con = open_duckdb(DB_PATH)
hard_failures: List[str] = []
try:
    ensure_meta_tables(con)

    core_stg_tables = [f"stg_{t}" for t in CORE_TABLES]
    loaded_stg_tables = list_stg_tables(con)

    # alles testen 
    stg_tables_to_check = sorted(set(core_stg_tables) | set(loaded_stg_tables))

    print(f"[plan] core_stg_tables       : {', '.join(core_stg_tables)}")
    print(f"[plan] loaded_stg_tables     : {len(loaded_stg_tables)} tables")
    print(f"[plan] stg_tables_to_check   : {len(stg_tables_to_check)} tables")
    print(f"[plan] STRICT_DQ             : {STRICT_DQ}")

    # ============================================================
    # 4A) Gate 1 - technische Checks (HARD)
    # ============================================================

    for stg in stg_tables_to_check:
        if not table_exists(con, stg):
            log_fail(con, "table_exists", stg, "HARD", 1, "missing staging table")
            hard_failures.append(f"{stg}: missing staging table")
            continue
        log_pass(con, "table_exists", stg, "HARD")

        cols_lc = columns_map_lower(con, stg)

        # tech cols
        missing_tech = [c for c in TECH_REQUIRED_COLS if c.lower() not in cols_lc]
        if missing_tech:
            log_fail(con, "required_tech_columns", stg, "HARD", len(missing_tech),
                     "missing: " + ", ".join(missing_tech))
            hard_failures.append(f"{stg}: missing tech cols {missing_tech}")
        else:
            log_pass(con, "required_tech_columns", stg, "HARD")

        # run_id consistency
        run_col = pick_col(cols_lc, "_run_id")
        if run_col:
            n_bad = scalar_int(con, f"""
                SELECT COUNT(*)
                FROM {stg}
                WHERE {run_col} IS NULL OR {run_col} <> ?
            """, [run_id])
            if n_bad == 0:
                log_pass(con, "run_id_consistency", stg, "HARD")
            else:
                log_fail(con, "run_id_consistency", stg, "HARD", n_bad,
                         "rows with _run_id != current run_id")
                hard_failures.append(f"{stg}: run_id mismatch rows={n_bad}")
        else:
            # tech cols check already fails; avoid double hard fail noise
            log_skip(con, "run_id_consistency", stg, "HARD", "missing _run_id column (covered by tech cols)")

    # ============================================================
    # 4B) Gate 1 - Key checks for main identity tables (HARD)
    # ============================================================

    def check_pk_not_null_unique(table: str, pk_candidates: Tuple[str, ...], label: str):
        if not table_exists(con, table):
            log_skip(con, f"{label}_pk_checks", table, "HARD", "table missing")
            return
        cols = columns_map_lower(con, table)
        pk = pick_col(cols, *pk_candidates)
        if not pk:
            log_fail(con, f"{label}_pk_present", table, "HARD", 1, f"missing PK column (expected one of {pk_candidates})")
            hard_failures.append(f"{table}: missing pk col")
            return

        n_null = scalar_int(con, f"SELECT COUNT(*) FROM {table} WHERE {pk} IS NULL")
        if n_null == 0:
            log_pass(con, f"{label}_pk_not_null", table, "HARD")
        else:
            log_fail(con, f"{label}_pk_not_null", table, "HARD", n_null, f"{pk} is NULL")
            hard_failures.append(f"{table}: pk null rows={n_null}")

        n_dup_groups = scalar_int(con, f"""
            SELECT COUNT(*) FROM (
              SELECT {pk}
              FROM {table}
              GROUP BY {pk}
              HAVING COUNT(*) > 1
            )
        """)
        if n_dup_groups == 0:
            log_pass(con, f"{label}_pk_unique", table, "HARD")
        else:
            log_fail(con, f"{label}_pk_unique", table, "HARD", n_dup_groups, f"duplicate groups on {pk}")
            hard_failures.append(f"{table}: duplicate pk groups={n_dup_groups}")

    # Patients/Person PK checks
    check_pk_not_null_unique("stg_patients", ("Id", "id"), "patients")
    check_pk_not_null_unique("stg_person", ("source_patient_id", "patient_id", "Id", "id"), "person")

    # Encounters/Visit PK checks
    check_pk_not_null_unique("stg_encounters", ("Id", "id"), "encounters")
    check_pk_not_null_unique("stg_visit", ("source_encounter_id", "encounter_id", "Id", "id"), "visit")

    # ============================================================
    # 4C) RI Checks (LEFT JOIN) + OK-Rates
    #     Default: SOFT (weil manche Tabellen optional sein können)
    # ============================================================

    def ri_check(
        child_table: str,
        parent_table: str,
        child_fk_candidates: Tuple[str, ...],
        parent_pk_candidates: Tuple[str, ...],
        check_name: str,
        severity: str = "SOFT",
        only_when_fk_not_null: bool = False,
    ):
        if not table_exists(con, child_table) or not table_exists(con, parent_table):
            log_skip(con, check_name, child_table, severity, "child or parent table missing")
            return

        ccols = columns_map_lower(con, child_table)
        pcols = columns_map_lower(con, parent_table)

        c_fk = pick_col(ccols, *child_fk_candidates)
        p_pk = pick_col(pcols, *parent_pk_candidates)
        if not c_fk or not p_pk:
            log_skip(con, check_name, child_table, severity, f"missing columns fk={c_fk} pk={p_pk}")
            return

        # counts
        child_total = scalar_int(con, f"SELECT COUNT(*) FROM {child_table}")
        fk_missing = scalar_int(con, f"SELECT COUNT(*) FROM {child_table} WHERE {c_fk} IS NULL")

        if only_when_fk_not_null:
            fk_not_found = scalar_int(con, f"""
                SELECT COUNT(*)
                FROM {child_table} c
                LEFT JOIN {parent_table} p
                  ON c.{c_fk} = p.{p_pk}
                WHERE c.{c_fk} IS NOT NULL
                  AND p.{p_pk} IS NULL
            """)
        else:
            fk_not_found = scalar_int(con, f"""
                SELECT COUNT(*)
                FROM {child_table} c
                LEFT JOIN {parent_table} p
                  ON c.{c_fk} = p.{p_pk}
                WHERE p.{p_pk} IS NULL
            """)

        checked_nonnull = max(child_total - fk_missing, 0)
        # ok rows = checked_nonnull - fk_not_found (as best-effort)
        ok = max(checked_nonnull - fk_not_found, 0)
        ok_rate = (ok / checked_nonnull) if checked_nonnull > 0 else 1.0

        # n_bad = fk_missing + fk_not_found (pragmatisch)
        n_bad = fk_missing + fk_not_found

        status = "PASS" if n_bad == 0 else "FAIL"
        msg = f"child_total={child_total}, fk_missing={fk_missing}, fk_not_found={fk_not_found}, ri_ok_rate={ok_rate*100:.2f}% (fk={c_fk} -> {parent_table}.{p_pk})"

        insert_dq(con, check_name=check_name, table_name=child_table,
                  severity=severity, n_bad=int(n_bad), status=status, message=msg)

        # escalations: wenn du Kernbeziehungen als HARD willst, setze severity=HARD beim Aufruf
        if severity == "HARD" and n_bad > 0:
            hard_failures.append(f"{child_table}: RI fail {check_name} n_bad={n_bad}")

    # --- RI: "Visit -> Person" (oder encounters->patients)
    ri_check(
        "stg_visit", "stg_person",
        child_fk_candidates=("source_patient_id", "patient_id", "Patient"),
        parent_pk_candidates=("source_patient_id", "patient_id", "Id"),
        check_name="ri_visit_to_person",
        severity="SOFT",
        only_when_fk_not_null=True,
    )

    # Synthea-Schema Kern: encounters.Patient -> patients.Id (kann HARD sein)
    ri_check(
        "stg_encounters", "stg_patients",
        child_fk_candidates=("Patient", "PATIENT", "patient"),
        parent_pk_candidates=("Id", "ID", "id"),
        check_name="ri_encounters_to_patients",
        severity="HARD",
        only_when_fk_not_null=True,
    )

    # Condition -> Person
    ri_check(
        "stg_condition", "stg_person",
        child_fk_candidates=("source_patient_id", "patient_id", "Patient"),
        parent_pk_candidates=("source_patient_id", "patient_id", "Id"),
        check_name="ri_condition_to_person",
        severity="SOFT",
        only_when_fk_not_null=True,
    )

    # Procedure -> Person
    ri_check(
        "stg_procedure", "stg_person",
        child_fk_candidates=("source_patient_id", "patient_id", "Patient"),
        parent_pk_candidates=("source_patient_id", "patient_id", "Id"),
        check_name="ri_procedure_to_person",
        severity="SOFT",
        only_when_fk_not_null=True,
    )

    # Lab/Obs -> Person
    ri_check(
        "stg_lab_obs", "stg_person",
        child_fk_candidates=("source_patient_id", "patient_id", "Patient"),
        parent_pk_candidates=("source_patient_id", "patient_id", "Id"),
        check_name="ri_labobs_to_person",
        severity="SOFT",
        only_when_fk_not_null=True,
    )

    # Medication -> Person
    ri_check(
        "stg_medication", "stg_person",
        child_fk_candidates=("source_patient_id", "patient_id", "Patient"),
        parent_pk_candidates=("source_patient_id", "patient_id", "Id"),
        check_name="ri_medication_to_person",
        severity="SOFT",
        only_when_fk_not_null=True,
    )

    # Condition -> Visit (Encounter)
    ri_check(
        "stg_condition", "stg_visit",
        child_fk_candidates=("source_encounter_id", "encounter_id", "Encounter", "ENCOUNTER"),
        parent_pk_candidates=("source_encounter_id", "encounter_id", "Id"),
        check_name="ri_condition_to_visit",
        severity="SOFT",
        only_when_fk_not_null=True,
    )

    # ============================================================
    # 4D) Plausibilität: Stop/End >= Start (SOFT)
    # ============================================================

    def plaus_stop_ge_start(table: str, start_candidates: Tuple[str, ...], stop_candidates: Tuple[str, ...], check_name: str):
        if not table_exists(con, table):
            log_skip(con, check_name, table, "SOFT", "table missing")
            return
        cols = columns_map_lower(con, table)
        start = pick_col(cols, *start_candidates)
        stop = pick_col(cols, *stop_candidates)
        if not start or not stop:
            log_skip(con, check_name, table, "SOFT", f"missing start/stop columns start={start} stop={stop}")
            return

        # count bad among rows where both not null and parseable
        n_bad = scalar_int(con, f"""
            SELECT COUNT(*)
            FROM {table}
            WHERE {start} IS NOT NULL AND {stop} IS NOT NULL
              AND try_cast({start} AS TIMESTAMP) IS NOT NULL
              AND try_cast({stop} AS TIMESTAMP) IS NOT NULL
              AND try_cast({stop}  AS TIMESTAMP) < try_cast({start} AS TIMESTAMP)
        """)
        status = "PASS" if n_bad == 0 else "FAIL"
        msg = f"{stop} < {start} (timestamp) on rows where both non-null+parseable" if n_bad else None
        insert_dq(con, check_name=check_name, table_name=table, severity="SOFT", n_bad=n_bad, status=status, message=msg)

    # Visits
    plaus_stop_ge_start("stg_visit", ("start_dt", "Start", "START"), ("end_dt", "Stop", "STOP"), "plaus_visit_end_ge_start")
    # Conditions
    plaus_stop_ge_start("stg_condition", ("start_dt", "Start", "START"), ("end_dt", "Stop", "STOP"), "plaus_condition_end_ge_start")
    # Medications
    plaus_stop_ge_start("stg_medication", ("start_dt", "Start", "START"), ("end_dt", "Stop", "STOP"), "plaus_medication_end_ge_start")
    # Procedures
    plaus_stop_ge_start("stg_procedure", ("start_dt", "Start", "START"), ("end_dt", "Stop", "STOP"), "plaus_procedure_end_ge_start")
    # Encounters (Synthea)
    plaus_stop_ge_start("stg_encounters", ("Start", "START"), ("Stop", "STOP"), "plaus_encounter_stop_ge_start")

    # ============================================================
    # 4E) Duplikate: Source-IDs + inhaltliche Dedupe-Keys (SOFT)
    # ============================================================

    def dup_groups(table: str, key_expr_sql: str, check_name: str):
        if not table_exists(con, table):
            log_skip(con, check_name, table, "SOFT", "table missing")
            return
        n_groups = scalar_int(con, f"""
            SELECT COUNT(*) FROM (
              SELECT {key_expr_sql}
              FROM {table}
              GROUP BY {key_expr_sql}
              HAVING COUNT(*) > 1
            )
        """)
        status = "PASS" if n_groups == 0 else "FAIL"
        msg = "duplicate groups detected" if n_groups else None
        insert_dq(con, check_name=check_name, table_name=table, severity="SOFT", n_bad=n_groups, status=status, message=msg)

    # Source-ID duplicates
    dup_groups("stg_person", "source_patient_id", "dup_person_source_patient_id")
    dup_groups("stg_visit", "source_encounter_id", "dup_visit_source_encounter_id")

    # Content duplicates (light composite keys)
    dup_groups("stg_condition",
               "source_patient_id, COALESCE(source_encounter_id,''), COALESCE(code,''), COALESCE(start_dt,'')",
               "dup_condition_patient_enc_code_start")
    dup_groups("stg_procedure",
               "source_patient_id, COALESCE(source_encounter_id,''), COALESCE(code,''), COALESCE(start_dt,'')",
               "dup_procedure_patient_enc_code_start")
    dup_groups("stg_lab_obs",
               "source_patient_id, COALESCE(source_encounter_id,''), COALESCE(code,''), COALESCE(obs_dt,''), COALESCE(value,'')",
               "dup_labobs_patient_enc_code_dt_value")
    dup_groups("stg_medication",
               "source_patient_id, COALESCE(source_encounter_id,''), COALESCE(code,''), COALESCE(start_dt,'')",
               "dup_medication_patient_enc_code_start")

finally:
    con.close()

# ============================================================
# 5) Manifest schreiben + Gate
# ============================================================

hard_fail_count = len(hard_failures)

manifest_out = dict(manifest_in)
manifest_out.update({
    "dq": {
        "layer": LAYER,
        "checked_at_utc": CHECKED_AT.isoformat(),
        "strict_dq": STRICT_DQ,
        "hard_fail_count": int(hard_fail_count),
        "hard_failures": hard_failures[:100],
        "meta_table": "meta_dq_results",
    }
})

manifest_out_path = MANIFEST_DIR / "manifest_staging_checked.json"
manifest_out_path.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

print("\n" + "-" * 70)
print("STAGING DQ - SUMMARY")
print("-" * 70)
print(f"[out] manifest            : {manifest_out_path}")
print(f"[out] hard_fail_count     : {hard_fail_count}")
if hard_failures:
    print("[out] first_failures      : " + " | ".join(hard_failures[:3]))

if STRICT_DQ and hard_fail_count > 0:
    print("\n[dq ] QUALITY GATE FAILED (STRICT_DQ=True)")
    raise SystemExit(2)

print("=" * 70 + "\n")

# ============================================================
# 6) HTML Report exportieren
# ============================================================

dq_html_path = STAGING_QC_DIR / "staging_dq_report.html"
con_ro = duckdb.connect(str(DB_PATH), read_only=True)

dq_df = con_ro.execute("""
    SELECT *
    FROM meta_dq_results
    WHERE run_id = ?
      AND layer = 'staging'
    ORDER BY
      CASE severity WHEN 'HARD' THEN 2 ELSE 1 END DESC,
      CASE status WHEN 'FAIL' THEN 3 WHEN 'ERROR' THEN 3 WHEN 'SKIP' THEN 1 ELSE 0 END DESC,
      table_name, check_name
""", [run_id]).fetchdf()

con_ro.close()

if dq_df.empty:
    summary_df = pd.DataFrame([{"severity": "-", "status": "-", "n_checks": 0, "n_bad_total": 0}])
else:
    summary_df = (
        dq_df.groupby(["severity", "status"], dropna=False)
        .agg(n_checks=("check_name", "count"), n_bad_total=("n_bad", "sum"))
        .reset_index()
        .sort_values(["severity", "status"])
    )

fail_df = dq_df[dq_df["status"].isin(["FAIL", "ERROR"])].copy() if not dq_df.empty else dq_df

css = """
<style>
  body { font-family: Arial, sans-serif; margin: 24px; }
  h1,h2 { margin-bottom: 8px; }
  .meta { color: #333; margin-bottom: 16px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0 24px 0; }
  th, td { border: 1px solid #ddd; padding: 8px; font-size: 13px; vertical-align: top; }
  th { background: #f3f3f3; }
  .pass { background: #eef9ee; }
  .fail { background: #fdeaea; }
  .skip { background: #f6f6f6; color: #666; }
  .hard { font-weight: 700; }
  code { background: #f7f7f7; padding: 2px 4px; border-radius: 3px; }
</style>
"""

def df_to_html(df: pd.DataFrame, *, highlight: bool = True) -> str:
    if df.empty:
        return "<p><em>No rows.</em></p>"

    def row_class(row):
        cls = []
        if highlight:
            if row.get("status") == "PASS": cls.append("pass")  # noqa: E701
            if row.get("status") in ("FAIL", "ERROR"): cls.append("fail")  # noqa: E701
            if row.get("status") == "SKIP": cls.append("skip")  # noqa: E701
        if row.get("severity") == "HARD": cls.append("hard")  # noqa: E701
        return " ".join(cls)

    cols = list(df.columns)
    head = "<tr>" + "".join(f"<th>{escape(str(c))}</th>" for c in cols) + "</tr>"
    rows = []
    for _, r in df.iterrows():
        cls = row_class(r)
        tds = "".join(f"<td>{escape('' if pd.isna(r[c]) else str(r[c]))}</td>" for c in cols)
        rows.append(f"<tr class='{cls}'>{tds}</tr>")
    return "<table>" + head + "".join(rows) + "</table>"

html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Staging DQ Report - {escape(run_id)}</title>
{css}
</head>
<body>
<h1>Staging DQ Report (Full)</h1>
<div class="meta">
  <div><strong>run_id:</strong> <code>{escape(run_id)}</code></div>
  <div><strong>checked_at (UTC):</strong> {escape(CHECKED_AT.isoformat())}</div>
  <div><strong>db:</strong> <code>{escape(DB_PATH.as_posix())}</code></div>
  <div><strong>strict_dq:</strong> {escape(str(STRICT_DQ))}</div>
  <div><strong>hard_fail_count:</strong> {escape(str(hard_fail_count))}</div>
</div>

<h2>Summary</h2>
{df_to_html(summary_df, highlight=False)}

<h2>Failures / Errors (quick view)</h2>
{df_to_html(fail_df, highlight=True)}

<h2>All Checks</h2>
{df_to_html(dq_df, highlight=True)}

</body>
</html>
"""

dq_html_path.write_text(html, encoding="utf-8")
print(f"[out] dq html report       : {dq_html_path}")
