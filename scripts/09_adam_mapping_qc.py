# ============================================================
# 09_adam_qc.py
# ADaM QC (minimal, prüfungsorientiert) – ADSL + ADBDS_LOS
#
# Ziele:
#  - 1 Zeile pro USUBJID in ADSL + ADBDS_LOS
#  - Coverage: LOS vorhanden? End imputiert/unresolved Raten
#  - Plausibilität: LOS >= 0, Outlier Heuristik, AGE plausibel
#  - RI: USUBJID in ADSL muss in SDTM DM existieren (und umgekehrt optional)
#  - Duplicate group heuristics
#
# Inputs:
#  - DuckDB Tabellen aus Step 08: adam_adsl, adam_adbds_los
#  - SDTM DM aus Step 06: sdtm_dm (für RI)
#  - Manifest: outputs/manifest_adam.json (fallback: manifest_sdtm_checked.json)
#
# Outputs:
#  - outputs/adam_qc/*.csv
#  - outputs/adam_qc/adam_qc_report.html
#  - outputs/adam_qc/chart_*.png
#  - outputs/manifest_adam_checked.json
# ============================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional, List, Dict

import duckdb
import pandas as pd
import matplotlib.pyplot as plt  # matplotlib only

from scripts.config import DB_PATH, OUT_DIR, ensure_dirs

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 09 – ADaM QC (ADSL + ADBDS_LOS)")
print("=" * 70)

# ============================================================
# 1) Manifest laden (Input)
# ============================================================

manifest_candidates = [
    OUT_DIR / "manifest_adam.json",
    OUT_DIR / "manifest_sdtm_checked.json",
    OUT_DIR / "manifest_sdtm.json",
]

manifest_in_path = next((p for p in manifest_candidates if p.exists()), None)
if manifest_in_path is None:
    raise FileNotFoundError(
        "Kein Manifest gefunden. Erwartet:\n"
        f" - {manifest_candidates[0]}\n"
        f" - {manifest_candidates[1]}\n"
        f" - {manifest_candidates[2]}\n"
        "Bitte zuerst Step 08 (und davor Step 06) ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
run_id = manifest_in.get("run_id")
if not run_id:
    raise ValueError("run_id fehlt im Manifest. Bitte Step 03/06/08 erneut ausführen.")

RUN_TS = datetime.now(timezone.utc)
STRICT_QC = bool(manifest_in.get("adam_qc", {}).get("strict_qc", False))  # default: report-only

print(f"[in ] manifest            : {manifest_in_path}")
print(f"[run] run_id              : {run_id}")
print(f"[db ] path                : {DB_PATH}")
print(f"[qc ] STRICT_QC           : {STRICT_QC}")

# ============================================================
# 2) Output dirs
# ============================================================

QC_DIR = OUT_DIR / "adam_qc"
QC_DIR.mkdir(parents=True, exist_ok=True)

CSV_COVERAGE = QC_DIR / "adam_qc_coverage.csv"
CSV_RI = QC_DIR / "adam_qc_ri.csv"
CSV_PLAUS = QC_DIR / "adam_qc_plausibility.csv"
CSV_DUPS = QC_DIR / "adam_qc_duplicates.csv"

REPORT_HTML = QC_DIR / "adam_qc_report.html"
RESULTS_JSON = QC_DIR / "adam_qc_summary.json"
MANIFEST_OUT = OUT_DIR / "manifest_adam_checked.json"

CHART_COVERAGE = QC_DIR / "chart_adam_coverage.png"
CHART_PLAUS_LOS = QC_DIR / "chart_adam_los_plausibility.png"
CHART_RI = QC_DIR / "chart_adam_ri.png"

# ============================================================
# 3) Helpers
# ============================================================

def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path))
    con.execute("PRAGMA threads=4")
    return con

def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ? LIMIT 1",
        [name],
    ).fetchone() is not None

def scalar_int(con: duckdb.DuckDBPyConnection, sql: str, params: Optional[List] = None) -> int:
    r = con.execute(sql, params or []).fetchone()
    return int(r[0]) if r and r[0] is not None else 0

def html_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "<p><em>(keine Daten)</em></p>"
    return df.to_html(index=False, escape=True)

def safe_pct(n: int, d: int) -> float:
    return round((n / d) * 100.0, 2) if d else 0.0

# ============================================================
# 4) Load dataframes from DuckDB
# ============================================================

con = connect(DB_PATH)
hard_failures: List[str] = []

try:
    required = ["adam_adsl", "adam_adbds_los", "sdtm_dm"]
    missing = [t for t in required if not table_exists(con, t)]
    if missing:
        raise RuntimeError(
            "Fehlende Tabellen für ADaM QC. Bitte Steps 06 & 08 ausführen.\n"
            f"Missing: {missing}"
        )

    adsl = con.execute("SELECT * FROM adam_adsl").fetchdf()
    los = con.execute("SELECT * FROM adam_adbds_los").fetchdf()
    dm = con.execute("SELECT * FROM sdtm_dm").fetchdf()

    # Normalize empty strings -> NA for key cols
    for df in (adsl, los, dm):
        if "USUBJID" in df.columns:
            df["USUBJID"] = df["USUBJID"].astype(str).replace({"": None, "nan": None, "None": None})

    # ============================================================
    # 5) QC A: Coverage / Completeness
    # ============================================================

    cov_rows: List[Dict] = []

    def cov_metric(df: pd.DataFrame, dataset: str, col: str) -> Dict:
        n = len(df)
        present = int(df[col].notna().sum()) if (col in df.columns and n) else 0
        missing = int(n - present) if n else 0
        return {
            "dataset": dataset,
            "metric": f"{col}_present_pct",
            "value": round((present / n) * 100.0, 2) if n else 100.0,
            "n_missing": missing,
            "n_total": n,
        }

    # ADSL: LOS fields + flags
    for c in ["INDEX_LOS_DAYS", "INDEX_END_IMPUTEDFL", "INDEX_END_MISSING_UNRESOLVEDFL", "AGE"]:
        if c in adsl.columns:
            cov_rows.append(cov_metric(adsl, "ADSL", c))

    # LOS dataset: AVAL + flags
    for c in ["AVAL", "END_IMPUTEDFL", "END_MISSING_UNRESOLVEDFL"]:
        if c in los.columns:
            cov_rows.append(cov_metric(los, "ADBDS_LOS", c))

    coverage_df = pd.DataFrame(cov_rows)
    coverage_df.to_csv(CSV_COVERAGE, index=False)

    # ============================================================
    # 6) QC B: RI checks (USUBJID in DM)
    # ============================================================

    # RI 1: ADSL.USUBJID must exist in DM.USUBJID
    adsl_missing_usubjid = int(adsl["USUBJID"].isna().sum()) if "USUBJID" in adsl.columns else len(adsl)
    adsl_fk_missing = 0
    if "USUBJID" in adsl.columns and "USUBJID" in dm.columns:
        dm_set = set(dm["USUBJID"].dropna().unique().tolist())
        adsl_fk_missing = int(adsl["USUBJID"].dropna().apply(lambda x: x not in dm_set).sum())

    # RI 2 (optional): DM subjects should exist in ADSL (reconciliation)
    dm_missing_usubjid = int(dm["USUBJID"].isna().sum()) if "USUBJID" in dm.columns else len(dm)
    dm_fk_missing = 0
    if "USUBJID" in dm.columns and "USUBJID" in adsl.columns:
        adsl_set = set(adsl["USUBJID"].dropna().unique().tolist())
        dm_fk_missing = int(dm["USUBJID"].dropna().apply(lambda x: x not in adsl_set).sum())

    ri_df = pd.DataFrame([
        {
            "check": "adsl_usubjid_not_null",
            "n_bad": adsl_missing_usubjid,
            "n_total": len(adsl),
            "bad_pct": safe_pct(adsl_missing_usubjid, len(adsl)),
            "severity": "HARD",
            "message": "USUBJID ist Schlüssel in ADaM; darf nicht fehlen",
        },
        {
            "check": "adsl_usubjid_in_dm",
            "n_bad": adsl_fk_missing,
            "n_total": len(adsl),
            "bad_pct": safe_pct(adsl_fk_missing, len(adsl)),
            "severity": "HARD",
            "message": "ADSL USUBJID muss in SDTM DM existieren",
        },
        {
            "check": "dm_usubjid_not_null",
            "n_bad": dm_missing_usubjid,
            "n_total": len(dm),
            "bad_pct": safe_pct(dm_missing_usubjid, len(dm)),
            "severity": "SOFT",
            "message": "DM USUBJID missing ist ungewöhnlich; sollte 0 sein",
        },
        {
            "check": "dm_subjects_in_adsl",
            "n_bad": dm_fk_missing,
            "n_total": len(dm),
            "bad_pct": safe_pct(dm_fk_missing, len(dm)),
            "severity": "SOFT",
            "message": "Recon: jedes DM Subject sollte eine ADSL Zeile haben (minimal)",
        },
    ])
    ri_df.to_csv(CSV_RI, index=False)

    # Hard failure criteria
    if adsl_missing_usubjid > 0:
        hard_failures.append(f"ADSL: USUBJID missing rows={adsl_missing_usubjid}")
    if adsl_fk_missing > 0:
        hard_failures.append(f"ADSL: USUBJID not in DM rows={adsl_fk_missing}")

    # ============================================================
    # 7) QC C: Plausibility checks (LOS + AGE)
    # ============================================================

    plaus_rows: List[Dict] = []

    # LOS negative should be 0 or null (after cap)
    if "INDEX_LOS_DAYS" in adsl.columns:
        n_bad_los_neg = int((adsl["INDEX_LOS_DAYS"].notna() & (adsl["INDEX_LOS_DAYS"] < 0)).sum())
        plaus_rows.append({
            "check": "adsl_index_los_non_negative",
            "n_bad": n_bad_los_neg,
            "n_total": len(adsl),
            "bad_pct": safe_pct(n_bad_los_neg, len(adsl)),
            "severity": "HARD",
            "message": "INDEX_LOS_DAYS < 0 (sollte nach Cap nicht vorkommen)",
        })
        if n_bad_los_neg > 0:
            hard_failures.append(f"ADSL: negative LOS rows={n_bad_los_neg}")

    # LOS extreme outliers (heuristic): > 365 days
    if "INDEX_LOS_DAYS" in adsl.columns:
        n_outlier_los = int((adsl["INDEX_LOS_DAYS"].notna() & (adsl["INDEX_LOS_DAYS"] > 365)).sum())
        plaus_rows.append({
            "check": "adsl_index_los_outlier_gt_365d",
            "n_bad": n_outlier_los,
            "n_total": len(adsl),
            "bad_pct": safe_pct(n_outlier_los, len(adsl)),
            "severity": "SOFT",
            "message": "Heuristik: LOS > 365d ist sehr ungewöhnlich (prüfen / dokumentieren)",
        })

    # AGE plausibility: [0, 120]
    if "AGE" in adsl.columns:
        n_age_missing = int(adsl["AGE"].isna().sum())
        n_age_bad = int(adsl["AGE"].dropna().apply(lambda x: (x < 0) or (x > 120)).sum()) if len(adsl) else 0
        plaus_rows.append({
            "check": "adsl_age_missing",
            "n_bad": n_age_missing,
            "n_total": len(adsl),
            "bad_pct": safe_pct(n_age_missing, len(adsl)),
            "severity": "SOFT",
            "message": "AGE ist optional; missing ok, aber sollte erklärbar sein (fehlendes BRTHDTC/Index Start)",
        })
        plaus_rows.append({
            "check": "adsl_age_range_0_120",
            "n_bad": n_age_bad,
            "n_total": len(adsl),
            "bad_pct": safe_pct(n_age_bad, len(adsl)),
            "severity": "HARD",
            "message": "AGE außerhalb [0,120] ist unplausibel",
        })
        if n_age_bad > 0:
            hard_failures.append(f"ADSL: AGE out of range rows={n_age_bad}")

    plaus_df = pd.DataFrame(plaus_rows)
    plaus_df.to_csv(CSV_PLAUS, index=False)

    # ============================================================
    # 8) QC D: Duplicate checks (hard uniqueness)
    # ============================================================

    dup_rows: List[Dict] = []

    def dup_groups(df: pd.DataFrame, dataset: str, keys: List[str], severity: str) -> Dict:
        missing_keys = [k for k in keys if k not in df.columns]
        if missing_keys:
            return {
                "dataset": dataset,
                "check": "dup_groups",
                "key_expr": ",".join(keys),
                "dup_groups": None,
                "severity": "SKIP",
                "message": f"missing keys: {missing_keys}",
            }
        g = df.groupby(keys, dropna=False).size()
        n_groups = int((g > 1).sum())
        return {
            "dataset": dataset,
            "check": "dup_groups",
            "key_expr": ",".join(keys),
            "dup_groups": n_groups,
            "severity": severity,
            "message": "duplicate groups detected" if n_groups else None,
        }

    # Hard: ADSL must be unique by USUBJID
    if "USUBJID" in adsl.columns:
        n_dup_usubjid = int(adsl.groupby(["USUBJID"]).size().gt(1).sum())
        dup_rows.append({
            "dataset": "ADSL",
            "check": "unique_usubjid",
            "key_expr": "USUBJID",
            "dup_groups": n_dup_usubjid,
            "severity": "HARD",
            "message": "ADSL muss 1 row/subject sein" if n_dup_usubjid else None,
        })
        if n_dup_usubjid > 0:
            hard_failures.append(f"ADSL: duplicate USUBJID groups={n_dup_usubjid}")

    # Hard: ADBDS_LOS must be unique by USUBJID
    if "USUBJID" in los.columns:
        n_dup_usubjid_los = int(los.groupby(["USUBJID"]).size().gt(1).sum())
        dup_rows.append({
            "dataset": "ADBDS_LOS",
            "check": "unique_usubjid",
            "key_expr": "USUBJID",
            "dup_groups": n_dup_usubjid_los,
            "severity": "HARD",
            "message": "ADBDS_LOS muss 1 row/subject sein" if n_dup_usubjid_los else None,
        })
        if n_dup_usubjid_los > 0:
            hard_failures.append(f"ADBDS_LOS: duplicate USUBJID groups={n_dup_usubjid_los}")

    # Soft: heuristic dup key on LOS content
    dup_rows.append(dup_groups(los, "ADBDS_LOS", ["USUBJID", "PARAMCD", "ADT", "AVAL"], "SOFT"))

    dups_df = pd.DataFrame(dup_rows)
    dups_df.to_csv(CSV_DUPS, index=False)

    # ============================================================
    # 9) Charts (Grafik Output)
    # ============================================================

    # Coverage chart: show present_pct for key metrics
    if not coverage_df.empty:
        cov_plot = coverage_df.copy()
        cov_plot["label"] = cov_plot["dataset"] + ":" + cov_plot["metric"]
        plt.figure()
        plt.bar(cov_plot["label"], cov_plot["value"])
        plt.xticks(rotation=45, ha="right")
        plt.ylabel("present_pct")
        plt.title("ADaM QC – Coverage (Present %)")
        plt.tight_layout()
        plt.savefig(CHART_COVERAGE, dpi=150)
        plt.close()

    # Plaus chart: show bad counts for plaus checks
    if not plaus_df.empty:
        plt.figure()
        plt.bar(plaus_df["check"], plaus_df["n_bad"])
        plt.xticks(rotation=45, ha="right")
        plt.ylabel("n_bad")
        plt.title("ADaM QC – Plausibility (Bad counts)")
        plt.tight_layout()
        plt.savefig(CHART_PLAUS_LOS, dpi=150)
        plt.close()

    # RI chart
    if not ri_df.empty:
        plt.figure()
        plt.bar(ri_df["check"], ri_df["n_bad"])
        plt.xticks(rotation=45, ha="right")
        plt.ylabel("n_bad")
        plt.title("ADaM QC – RI checks (Bad counts)")
        plt.tight_layout()
        plt.savefig(CHART_RI, dpi=150)
        plt.close()

    # ============================================================
    # 10) Summary JSON + HTML
    # ============================================================

    hard_fail_count = len(hard_failures)

    summary = {
        "run_id": run_id,
        "run_ts_utc": RUN_TS.isoformat(),
        "db_path": DB_PATH.as_posix(),
        "strict_qc": STRICT_QC,
        "hard_fail_count": hard_fail_count,
        "hard_failures": hard_failures[:200],
        "outputs": {
            "coverage_csv": CSV_COVERAGE.as_posix(),
            "ri_csv": CSV_RI.as_posix(),
            "plausibility_csv": CSV_PLAUS.as_posix(),
            "duplicates_csv": CSV_DUPS.as_posix(),
            "html_report": REPORT_HTML.as_posix(),
            "charts": {
                "coverage": CHART_COVERAGE.as_posix(),
                "plausibility": CHART_PLAUS_LOS.as_posix(),
                "ri": CHART_RI.as_posix(),
            },
        },
    }
    RESULTS_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # HTML report
    css = """
    <style>
      body { font-family: Arial, sans-serif; margin: 24px; }
      h1,h2 { margin-bottom: 8px; }
      .meta { color: #333; margin-bottom: 16px; }
      table { border-collapse: collapse; width: 100%; margin: 10px 0 22px 0; }
      th, td { border: 1px solid #ddd; padding: 8px; font-size: 13px; }
      th { background: #f3f3f3; }
      .hard { font-weight: 700; }
      .fail { background: #fdeaea; }
      code { background: #f7f7f7; padding: 2px 4px; border-radius: 3px; }
      img { max-width: 100%; border: 1px solid #ddd; }
    </style>
    """

    def mark_fail(df: pd.DataFrame) -> str:
        if df is None or df.empty:
            return "<p><em>(keine Daten)</em></p>"
        d = df.copy()
        # simple class marker
        if "severity" in d.columns:
            d["severity"] = d["severity"].astype(str)
        html = d.to_html(index=False, escape=True)
        return html

    html_doc = f"""<!doctype html>
    <html><head><meta charset="utf-8"/><title>ADaM QC Report</title>{css}</head>
    <body>
      <h1>STEP 09 – ADaM QC Report</h1>
      <div class="meta">
        <div><b>run_id</b>: <code>{escape(run_id)}</code></div>
        <div><b>run_ts_utc</b>: {escape(RUN_TS.isoformat())}</div>
        <div><b>db</b>: <code>{escape(DB_PATH.as_posix())}</code></div>
        <div><b>strict_qc</b>: {escape(str(STRICT_QC))}</div>
        <div><b>hard_fail_count</b>: {escape(str(hard_fail_count))}</div>
      </div>

      <h2>Coverage</h2>
      {html_table(coverage_df)}

      <h2>RI</h2>
      {html_table(ri_df)}

      <h2>Plausibility</h2>
      {html_table(plaus_df)}

      <h2>Duplicates</h2>
      {html_table(dups_df)}

      <h2>Charts</h2>
      <p><b>Coverage</b><br><img src="{escape(CHART_COVERAGE.name)}"></p>
      <p><b>Plausibility</b><br><img src="{escape(CHART_PLAUS_LOS.name)}"></p>
      <p><b>RI</b><br><img src="{escape(CHART_RI.name)}"></p>
    </body></html>
    """
    REPORT_HTML.write_text(html_doc, encoding="utf-8")

    # ============================================================
    # 11) Manifest update
    # ============================================================

    manifest_out = dict(manifest_in)
    manifest_out.update({
        "adam_qc": {
            "run_id": run_id,
            "checked_at_utc": RUN_TS.isoformat(),
            "strict_qc": STRICT_QC,
            "hard_fail_count": hard_fail_count,
            "hard_failures": hard_failures[:200],
            "outputs": summary["outputs"],
        }
    })
    MANIFEST_OUT.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

    print("\n" + "-" * 70)
    print("ADaM QC - SUMMARY")
    print("-" * 70)
    print(f"[out] coverage_csv        : {CSV_COVERAGE}")
    print(f"[out] ri_csv              : {CSV_RI}")
    print(f"[out] plausibility_csv    : {CSV_PLAUS}")
    print(f"[out] duplicates_csv      : {CSV_DUPS}")
    print(f"[out] html report         : {REPORT_HTML}")
    print(f"[out] summary json        : {RESULTS_JSON}")
    print(f"[out] manifest            : {MANIFEST_OUT}")
    print(f"[qc ] hard_fail_count     : {hard_fail_count}")
    if hard_failures:
        print("[qc ] first_failures      : " + " | ".join(hard_failures[:3]))

    if STRICT_QC and hard_fail_count > 0:
        print("\n[dq ] QUALITY GATE FAILED (STRICT_QC=True)")
        raise SystemExit(2)

    print("=" * 70 + "\n")
    print("STEP 09 DONE")

finally:
    try:
        con.close()
    except Exception:
        pass
