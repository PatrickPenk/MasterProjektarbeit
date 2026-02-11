# ============================================================
# 08_adam_mapping.py
# ============================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from typing import Dict, Optional, List

import duckdb

from scripts.config import DB_PATH, MANIFEST_DIR, ADAM_DIR, ensure_dirs

ensure_dirs()

print("\n" + "=" * 60)
print("STEP 08 – ADaM MAPPING (SDTM → ADaM: ADSL + ADBDS_LOS)")
print("=" * 60)

# ============================================================
# 1) Manifest laden (Input aus Step 06/07)
# ============================================================

manifest_candidates = [
    MANIFEST_DIR / "manifest_sdtm_checked.json",
    MANIFEST_DIR / "manifest_sdtm.json",
]

manifest_in_path = next((p for p in manifest_candidates if p.exists()), None)
if manifest_in_path is None:
    raise FileNotFoundError(
        "Kein SDTM-Manifest gefunden. Erwartet:\n"
        f" - {manifest_candidates[0]}\n"
        f" - {manifest_candidates[1]}\n"
        "Bitte zuerst Step 06 (und optional Step 07) ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
run_id = manifest_in.get("run_id")
if not run_id:
    raise ValueError("run_id fehlt im Manifest. Bitte Step 03/06 erneut ausführen.")

RUN_TS = datetime.now(timezone.utc)

print(f"[in ] manifest            : {manifest_in_path}")
print(f"[run] run_id              : {run_id}")
print(f"[db ] path                : {DB_PATH}")

# ============================================================
# 2) Output Pfade
# ============================================================

ADAM_DIR.mkdir(parents=True, exist_ok=True)

ADSL_CSV = ADAM_DIR / "adsl.csv"
ADBDS_LOS_CSV = ADAM_DIR / "adbds_los.csv"

REPORT_HTML = ADAM_DIR / "adam_mapping_report.html"
RESULTS_JSON = ADAM_DIR / "adam_mapping_results.json"
MANIFEST_OUT_PATH = MANIFEST_DIR / "manifest_adam.json"

STUDYID_DEFAULT = "SYNTH-01"
STUDYID = (
    manifest_in.get("study", {}).get("studyid")
    or manifest_in.get("sdtm", {}).get("studyid")
    or STUDYID_DEFAULT
)

# ============================================================
# 3) Helpers
# ============================================================

def connect_with_retry(db_path: str, retries: int = 20, sleep_s: float = 0.5) -> duckdb.DuckDBPyConnection:
    import time
    last_err = None
    for _ in range(retries):
        try:
            return duckdb.connect(db_path)
        except Exception as e:
            last_err = e
            time.sleep(sleep_s)
    raise RuntimeError(f"DuckDB ist gesperrt oder nicht erreichbar: {db_path}\nLetzter Fehler: {last_err}") from last_err

def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ? LIMIT 1",
        [name],
    ).fetchone() is not None

def scalar_int(con: duckdb.DuckDBPyConnection, sql: str, params: Optional[List] = None) -> int:
    r = con.execute(sql, params or []).fetchone()
    return int(r[0]) if r and r[0] is not None else 0

def html_table(headers: List[str], rows: List[List]) -> str:
    th = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{escape('' if c is None else str(c))}</td>" for c in r) + "</tr>"
        for r in rows
    )
    return (
        "<table style='border-collapse:collapse;width:100%;margin:12px 0;'>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
    )

# ============================================================
# 4) Preconditions
# ============================================================

con = connect_with_retry(str(DB_PATH))
con.execute("PRAGMA threads=4")

try:
    required = ["sdtm_dm", "sdtm_sv"]
    missing = [t for t in required if not table_exists(con, t)]
    if missing:
        raise RuntimeError(
            "Fehlende SDTM Eingabetabellen. Bitte Step 06 ausführen.\n"
            f"Missing: {missing}"
        )

    # ============================================================
    # 5) Build LOS view: index visit pro USUBJID 
    # ============================================================

    con.execute("""
    CREATE OR REPLACE TEMP VIEW _sv_los_base AS
    SELECT
        STUDYID,
        DOMAIN,
        USUBJID,
        VISITNUM,
        VISIT,
        SVSTDTC,
        SVENDTC,
        TRY_CAST(SVSTDTC AS TIMESTAMP) AS SVSTDTC_ts,
        TRY_CAST(SVENDTC AS TIMESTAMP) AS SVENDTC_ts
    FROM sdtm_sv
    """)

    con.execute("""
    CREATE OR REPLACE TEMP VIEW _sv_los_seq AS
    SELECT
        *,
        LEAD(SVSTDTC_ts) OVER (
            PARTITION BY USUBJID
            ORDER BY SVSTDTC_ts, VISITNUM
        ) AS NEXT_SVSTDTC_ts
    FROM _sv_los_base
    """)

    con.execute("""
    CREATE OR REPLACE TEMP VIEW _sv_los_calc AS
    SELECT
        STUDYID,
        USUBJID,
        VISITNUM,
        VISIT,
        SVSTDTC,
        SVENDTC,

        SVSTDTC_ts,
        SVENDTC_ts,
        NEXT_SVSTDTC_ts,

        CASE WHEN SVENDTC_ts IS NULL THEN 1 ELSE 0 END AS END_IMPUTEDFL,
        CASE WHEN SVENDTC_ts IS NULL AND NEXT_SVSTDTC_ts IS NULL THEN 1 ELSE 0 END AS END_MISSING_UNRESOLVEDFL,

        CASE
          WHEN SVSTDTC_ts IS NULL THEN NULL
          WHEN COALESCE(SVENDTC_ts, NEXT_SVSTDTC_ts) IS NULL THEN NULL
          ELSE COALESCE(SVENDTC_ts, NEXT_SVSTDTC_ts)
        END AS SVENDTC_IMP_ts,

        CASE
          WHEN SVSTDTC_ts IS NULL THEN NULL
          WHEN COALESCE(SVENDTC_ts, NEXT_SVSTDTC_ts) IS NULL THEN NULL
          ELSE
            GREATEST(
              0.0,
              DATE_DIFF('second', SVSTDTC_ts, COALESCE(SVENDTC_ts, NEXT_SVSTDTC_ts)) / 86400.0
            )
        END AS LOS_DAYS
    FROM _sv_los_seq
    """)

    # Index visit = first record per subject based on (SVSTDTC_ts, VISITNUM)
    con.execute("""
    CREATE OR REPLACE TEMP VIEW _sv_index_visit AS
    SELECT *
    FROM (
      SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY USUBJID
            ORDER BY SVSTDTC_ts, VISITNUM
        ) AS _rn
      FROM _sv_los_calc
    ) x
    WHERE _rn = 1
    """)

    # ============================================================
    # 6) ADBDS_LOS (1 row per USUBJID, index visit)
    # ============================================================

    con.execute("""
    CREATE OR REPLACE TABLE adam_adbds_los AS
    SELECT
        STUDYID,
        USUBJID,
        'LOS' AS PARAMCD,
        'Length of Stay (days) - index visit' AS PARAM,

        LOS_DAYS AS AVAL,

        SVSTDTC AS ADT,
        SVSTDTC AS ADTM,

        SVSTDTC AS STARTDTC,
        SVENDTC AS ENDDTC,

        CASE
          WHEN SVENDTC_IMP_ts IS NULL THEN NULL
          ELSE STRFTIME(SVENDTC_IMP_ts, '%Y-%m-%dT%H:%M:%S')
        END AS ENDDTC_IMP,

        END_IMPUTEDFL,
        END_MISSING_UNRESOLVEDFL,

        VISITNUM,
        VISIT
    FROM _sv_index_visit
    """)

    # ============================================================
    # 7) ADSL (1 row per USUBJID) aus DM + Index-Visit Infos + AGE
    # ============================================================

    # Dedup DM on USUBJID
    con.execute("""
    CREATE OR REPLACE TEMP VIEW _dm_dedup AS
    SELECT *
    FROM (
      SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY USUBJID ORDER BY USUBJID) AS _rn
      FROM sdtm_dm
    ) d
    WHERE _rn = 1
    """)

    con.execute("""
    CREATE OR REPLACE TABLE adam_adsl AS
    SELECT
        d.STUDYID,
        d.USUBJID,
        d.USUBJID AS SUBJID,

        d.SEX,
        d.BRTHDTC,
        d.DTHDTC,
        d.RACE,
        d.ETHNIC,

        iv.VISITNUM AS INDEX_VISITNUM,
        iv.VISIT    AS INDEX_VISIT,
        iv.LOS_DAYS AS INDEX_LOS_DAYS,
        iv.END_IMPUTEDFL AS INDEX_END_IMPUTEDFL,
        iv.END_MISSING_UNRESOLVEDFL AS INDEX_END_MISSING_UNRESOLVEDFL,

        -- AGE (optional): floor((index_start - birth)/365.25)
        CASE
          WHEN TRY_CAST(d.BRTHDTC AS TIMESTAMP) IS NULL THEN NULL
          WHEN iv.SVSTDTC_ts IS NULL THEN NULL
          ELSE CAST(
            FLOOR(
              DATE_DIFF('second', TRY_CAST(d.BRTHDTC AS TIMESTAMP), iv.SVSTDTC_ts) / (365.25 * 86400.0)
            ) AS BIGINT
          )
        END AS AGE

    FROM _dm_dedup d
    LEFT JOIN _sv_index_visit iv
      ON d.USUBJID = iv.USUBJID
    """)

    # ============================================================
    # 8) CSV Exporte
    # ============================================================

    con.execute(f"COPY adam_adsl TO '{ADSL_CSV.as_posix()}' (HEADER, DELIMITER ',')")
    con.execute(f"COPY adam_adbds_los TO '{ADBDS_LOS_CSV.as_posix()}' (HEADER, DELIMITER ',')")

    # ============================================================
    # 9) Checks + Metrics
    # ============================================================

    adsl_n = scalar_int(con, "SELECT COUNT(*) FROM adam_adsl")
    adsl_uniq = scalar_int(con, "SELECT COUNT(*) FROM (SELECT USUBJID FROM adam_adsl GROUP BY USUBJID HAVING COUNT(*) > 1) x")
    adbds_n = scalar_int(con, "SELECT COUNT(*) FROM adam_adbds_los")
    adbds_uniq = scalar_int(con, "SELECT COUNT(*) FROM (SELECT USUBJID FROM adam_adbds_los GROUP BY USUBJID HAVING COUNT(*) > 1) x")

    idx_los_missing_n = scalar_int(con, "SELECT COUNT(*) FROM adam_adsl WHERE INDEX_LOS_DAYS IS NULL")
    idx_end_imputed_n = scalar_int(con, "SELECT COUNT(*) FROM adam_adsl WHERE COALESCE(INDEX_END_IMPUTEDFL,0) = 1")
    idx_end_unresolved_n = scalar_int(con, "SELECT COUNT(*) FROM adam_adsl WHERE COALESCE(INDEX_END_MISSING_UNRESOLVEDFL,0) = 1")

    idx_los_missing_pct = round((idx_los_missing_n / adsl_n) * 100, 2) if adsl_n else 0.0
    idx_end_imputed_pct = round((idx_end_imputed_n / adsl_n) * 100, 2) if adsl_n else 0.0
    idx_end_unresolved_pct = round((idx_end_unresolved_n / adsl_n) * 100, 2) if adsl_n else 0.0

    checks = {
        "adsl_rows": adsl_n,
        "adsl_dup_usubjid_groups": adsl_uniq,
        "adbds_los_rows": adbds_n,
        "adbds_los_dup_usubjid_groups": adbds_uniq,
        "index_los_missing_pct": idx_los_missing_pct,
        "index_end_imputed_pct": idx_end_imputed_pct,
        "index_end_unresolved_pct": idx_end_unresolved_pct,
    }

    print("\n" + "-" * 60)
    print("ADaM STEP 08 – SUMMARY CHECKS")
    print("-" * 60)
    print(f"[out] adsl.csv           : {ADSL_CSV}")
    print(f"[out] adbds_los.csv      : {ADBDS_LOS_CSV}")
    print(f"[chk] ADSL rows          : {adsl_n} (dup USUBJID groups={adsl_uniq})")
    print(f"[chk] ADBDS_LOS rows     : {adbds_n} (dup USUBJID groups={adbds_uniq})")
    print(f"[chk] Index LOS missing% : {idx_los_missing_pct}%")
    print(f"[chk] Index end imputed% : {idx_end_imputed_pct}%")
    print(f"[chk] Index end unresolved%: {idx_end_unresolved_pct}%")

    # ============================================================
    # 10) Results JSON + Mini HTML Report
    # ============================================================

    payload: Dict = {
        "run_id": run_id,
        "studyid": STUDYID,
        "run_ts_utc": RUN_TS.isoformat(),
        "db_path": str(DB_PATH),
        "outputs": {
            "adsl_csv": ADSL_CSV.as_posix(),
            "adbds_los_csv": ADBDS_LOS_CSV.as_posix(),
        },
        "tables": ["adam_adsl", "adam_adbds_los"],
        "checks": checks,
        "rules": {
            "index_visit": "first SV per USUBJID by SVSTDTC then VISITNUM",
            "los_days": "end-start in days; if end missing impute with next start; unresolved -> NULL; capped at 0",
            "age": "floor((index_start - birth)/365.25) if possible",
        },
    }

    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # HTML content (small, readable)
    html_sections = []
    html_sections.append("<h1>STEP 08 – ADaM Mapping Report</h1>")
    html_sections.append(
        f"<p><b>run_id</b>: {escape(run_id)}<br>"
        f"<b>studyid</b>: {escape(STUDYID)}<br>"
        f"<b>run_ts_utc</b>: {escape(RUN_TS.isoformat())}<br>"
        f"<b>db</b>: <code>{escape(DB_PATH.as_posix())}</code></p>"
    )

    html_sections.append("<h2>Outputs</h2>")
    html_sections.append("<ul>")
    html_sections.append(f"<li><code>{escape(ADSL_CSV.as_posix())}</code></li>")
    html_sections.append(f"<li><code>{escape(ADBDS_LOS_CSV.as_posix())}</code></li>")
    html_sections.append(f"<li><code>{escape(RESULTS_JSON.as_posix())}</code></li>")
    html_sections.append("</ul>")

    html_sections.append("<h2>Checks</h2>")
    html_sections.append(
        html_table(
            headers=["metric", "value"],
            rows=[[k, v] for k, v in checks.items()],
        )
    )

    html_sections.append("<h2>Rules (prüfungsorientiert)</h2>")
    html_sections.append("<ul>")
    html_sections.append("<li><b>Index-Visit</b>: erster Visit pro Subject (SVSTDTC, Tie → VISITNUM)</li>")
    html_sections.append("<li><b>LOS</b>: (END−START) in Tagen; END missing → impute mit nächstem START; unresolved → NA; negative → 0</li>")
    html_sections.append("<li><b>ADSL</b>: 1 Zeile pro USUBJID (DM), angereichert um Index-Visit + AGE (falls möglich)</li>")
    html_sections.append("</ul>")

    html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>ADaM Mapping Report</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 24px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 13px; }}
  th {{ background: #f3f3f3; }}
  code {{ background: #f7f7f7; padding: 2px 4px; border-radius: 3px; }}
</style>
</head>
<body>
{''.join(html_sections)}
</body>
</html>
"""
    REPORT_HTML.write_text(html_doc, encoding="utf-8")

    # ============================================================
    # 11) Manifest schreiben (mapping style wie andere Steps)
    # ============================================================

    manifest_out = dict(manifest_in)
    manifest_out.update({
        "adam": {
            "run_id": run_id,
            "run_ts_utc": RUN_TS.isoformat(),
            "studyid": STUDYID,
            "db_path": DB_PATH.as_posix(),
            "tables": ["adam_adsl", "adam_adbds_los"],
            "outputs": {
                "adsl_csv": ADSL_CSV.as_posix(),
                "adbds_los_csv": ADBDS_LOS_CSV.as_posix(),
                "results_json": RESULTS_JSON.as_posix(),
                "html_report": REPORT_HTML.as_posix(),
            },
            "checks": checks,
        }
    })

    MANIFEST_OUT_PATH.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

    print("\n[ok] wrote:", RESULTS_JSON)
    print("[ok] wrote:", REPORT_HTML)
    print("[out] manifest:", MANIFEST_OUT_PATH)
    print("\nSTEP 08 DONE – ADaM datasets created: ADSL, ADBDS_LOS")

finally:
    try:
        con.close()
    except Exception:
        pass
