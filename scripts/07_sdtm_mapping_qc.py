# ============================================================
# 07_sdtm_mapping_qc.py
# ============================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb

# Grafik + kleine DataFrames für Charts/Exports
import matplotlib.pyplot as plt
import pandas as pd

from scripts.config import DB_PATH, MANIFEST_DIR, SDTM_QC_DIR, ensure_dirs

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 07 – SDTM MAPPING QC (COVERAGE + UNMAPPED + TIMING + EXTRA QC + REPORT + CHARTS)")
print("=" * 70)

# ============================================================
# 1) Manifest laden (Input aus Step 06)
# ============================================================

manifest_in_path = MANIFEST_DIR / "manifest_sdtm.json"

if not manifest_in_path.exists():
    raise FileNotFoundError(
        f"Manifest nicht gefunden: {manifest_in_path}\n"
        "Bitte zuerst Step 06 ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
run_id = manifest_in.get("run_id")
if not run_id:
    raise ValueError("run_id fehlt im Manifest. Bitte Step 03/06 erneut ausführen.")

CHECKED_AT = datetime.now(timezone.utc)

print(f"[in ] manifest            : {manifest_in_path}")
print(f"[run] run_id              : {run_id}")
print(f"[db ] path                : {DB_PATH}")

# ============================================================
# 2) Outputs
# ============================================================

QC_DIR = SDTM_QC_DIR
QC_DIR.mkdir(parents=True, exist_ok=True)

COVERAGE_CSV = QC_DIR / "sdtm_mapping_coverage.csv"
UNMAPPED_CSV = QC_DIR / "sdtm_top_unmapped.csv"
TIMING_CSV   = QC_DIR / "sdtm_timing_plausibility.csv"

# Extra QC outputs
RI_TO_DM_CSV = QC_DIR / "sdtm_ri_to_dm_missing_usubjid.csv"
DUP_GROUPS_CSV = QC_DIR / "sdtm_duplicate_groups.csv"

SUMMARY_JSON = QC_DIR / "sdtm_mapping_qc_summary.json"
REPORT_HTML  = QC_DIR / "sdtm_mapping_qc_report.html"

CHART_COVERAGE_PNG = QC_DIR / "chart_coverage_code_label.png"
CHART_TIMING_BAD_PNG = QC_DIR / "chart_timing_bad_end_lt_start.png"
CHART_TIMING_MISSING_END_PNG = QC_DIR / "chart_timing_missing_end_pct.png"
CHART_RI_MISSING_PNG = QC_DIR / "chart_ri_missing_usubjid.png"

MANIFEST_OUT_PATH = MANIFEST_DIR / "manifest_sdtm_checked.json"

# Gate behavior (optional)
STRICT_QC = False  # set True if you want to fail pipeline on hard_failures

# ============================================================
# 3) Helpers
# ============================================================

def open_duckdb(db_path: Path) -> duckdb.DuckDBPyConnection:
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

def html_table(headers: List[str], rows: List[Tuple]) -> str:
    th = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{escape('' if c is None else str(c))}</td>" for c in r) + "</tr>"
        for r in rows
    )
    return (
        "<table>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
    )

# ============================================================
# 4) QC Definitions 
# ============================================================

# Coverage: domain -> (table, code_col, label_col)
COVERAGE_SPECS: List[Tuple[str, str, Optional[str], Optional[str]]] = [
    ("DM", "sdtm_dm", None, None),
    ("SV", "sdtm_sv", None, "VISIT"),
    ("LB", "sdtm_lb", "LBTESTCD", "LBTEST"),
    ("VS", "sdtm_vs", "VSTESTCD", "VSTEST"),
    ("MH", "sdtm_mh", None, "MHTERM"),
    ("CM", "sdtm_cm", None, "CMTRT"),
    ("PR", "sdtm_pr", None, "PRTRT"),
]

# Top Unmapped (wo Code+Label wirklich existiert)
UNMAPPED_SPECS: List[Tuple[str, str, str, str]] = [
    ("LB", "sdtm_lb", "LBTESTCD", "LBTEST"),
    ("VS", "sdtm_vs", "VSTESTCD", "VSTEST"),
]

# Timing plausibility domains (Stop>=Start + Missing Stop Rate)
TIMING_SPECS: List[Tuple[str, str, str, str]] = [
    ("SV", "sdtm_sv", "SVSTDTC", "SVENDTC"),
    ("MH", "sdtm_mh", "MHSTDTC", "MHENDTC"),
    ("CM", "sdtm_cm", "CMSTDTC", "CMENDTC"),
    ("PR", "sdtm_pr", "PRSTDTC", "PRENDTC"),
]

# Extra QC A: RI to DM (all domains except DM)
RI_TABLES: List[Tuple[str, str]] = [
    ("SV", "sdtm_sv"),
    ("LB", "sdtm_lb"),
    ("VS", "sdtm_vs"),
    ("MH", "sdtm_mh"),
    ("CM", "sdtm_cm"),
    ("PR", "sdtm_pr"),
]

# Extra QC B: Duplicate group heuristics (domain -> table, key expr)
DUP_SPECS: List[Tuple[str, str, str]] = [
    ("SV", "sdtm_sv", "USUBJID, SVSTDTC, COALESCE(VISIT,'')"),
    ("LB", "sdtm_lb", "USUBJID, LBDTC, COALESCE(LBTESTCD,''), COALESCE(LBORRES,'')"),
    ("VS", "sdtm_vs", "USUBJID, VSDTC, COALESCE(VSTESTCD,''), COALESCE(VSORRES,'')"),
    ("MH", "sdtm_mh", "USUBJID, MHSTDTC, COALESCE(MHTERM,'')"),
    ("CM", "sdtm_cm", "USUBJID, CMSTDTC, COALESCE(CMTRT,'')"),
    ("PR", "sdtm_pr", "USUBJID, PRSTDTC, COALESCE(PRTRT,'')"),
]

# ============================================================
# 5) Execute QC
# ============================================================

con = open_duckdb(DB_PATH)
hard_failures: List[str] = []

coverage_rows: List[Dict] = []
timing_rows: List[Dict] = []
ri_rows: List[Dict] = []
dup_rows: List[Dict] = []

try:
    # ---- Preconditions
    required_tables = sorted({t for _, t, _, _ in COVERAGE_SPECS})
    missing = [t for t in required_tables if not table_exists(con, t)]
    if missing:
        raise RuntimeError(
            "Fehlende SDTM Tabellen. Bitte Step 06 ausführen.\n"
            f"Missing: {missing}"
        )

    # ------------------------------------------------------------
    # 5.1 Coverage Report
    # ------------------------------------------------------------
    for domain, table, code_col, label_col in COVERAGE_SPECS:
        n = scalar_int(con, f"SELECT COUNT(*) FROM {table}")
        row = {"DOMAIN": domain, "N": n}

        if code_col:
            miss_code = scalar_int(con, f"""
                SELECT COUNT(*) FROM {table}
                WHERE {code_col} IS NULL
                   OR TRIM(CAST({code_col} AS VARCHAR)) = ''
                   OR UPPER(TRIM(CAST({code_col} AS VARCHAR))) IN ('UNKNOWN','UNK')
            """)
            row["code_present_pct"] = round((1 - (miss_code / n)) * 100, 2) if n else 100.0
            row["code_missing_n"] = miss_code
        else:
            row["code_present_pct"] = None
            row["code_missing_n"] = None

        if label_col:
            miss_label = scalar_int(con, f"""
                SELECT COUNT(*) FROM {table}
                WHERE {label_col} IS NULL
                   OR TRIM(CAST({label_col} AS VARCHAR)) = ''
                   OR UPPER(TRIM(CAST({label_col} AS VARCHAR))) IN ('UNKNOWN','UNK')
            """)
            row["label_present_pct"] = round((1 - (miss_label / n)) * 100, 2) if n else 100.0
            row["label_missing_n"] = miss_label
        else:
            row["label_present_pct"] = None
            row["label_missing_n"] = None

        coverage_rows.append(row)

    coverage_df = pd.DataFrame(coverage_rows)
    coverage_df.to_csv(COVERAGE_CSV, index=False)

    # ------------------------------------------------------------
    # 5.2 Top Unmapped (LB/VS)
    # ------------------------------------------------------------
    union_parts = []
    for domain, table, code_col, label_col in UNMAPPED_SPECS:
        union_parts.append(f"""
        SELECT
            '{domain}' AS DOMAIN,
            CASE
              WHEN ( {code_col} IS NULL OR TRIM(CAST({code_col} AS VARCHAR)) = '' OR UPPER(TRIM(CAST({code_col} AS VARCHAR))) IN ('UNKNOWN','UNK') )
               AND ( {label_col} IS NULL OR TRIM(CAST({label_col} AS VARCHAR)) = '' OR UPPER(TRIM(CAST({label_col} AS VARCHAR))) IN ('UNKNOWN','UNK') )
                THEN 'CODE+LABEL'
              WHEN ( {code_col} IS NULL OR TRIM(CAST({code_col} AS VARCHAR)) = '' OR UPPER(TRIM(CAST({code_col} AS VARCHAR))) IN ('UNKNOWN','UNK') )
                THEN 'CODE'
              WHEN ( {label_col} IS NULL OR TRIM(CAST({label_col} AS VARCHAR)) = '' OR UPPER(TRIM(CAST({label_col} AS VARCHAR))) IN ('UNKNOWN','UNK') )
                THEN 'LABEL'
              ELSE 'NONE'
            END AS missing_what,
            CAST({code_col} AS VARCHAR)  AS code,
            CAST({label_col} AS VARCHAR) AS label
        FROM {table}
        WHERE
            ( {code_col} IS NULL OR TRIM(CAST({code_col} AS VARCHAR)) = '' OR UPPER(TRIM(CAST({code_col} AS VARCHAR))) IN ('UNKNOWN','UNK') )
         OR ( {label_col} IS NULL OR TRIM(CAST({label_col} AS VARCHAR)) = '' OR UPPER(TRIM(CAST({label_col} AS VARCHAR))) IN ('UNKNOWN','UNK') )
        """)

    union_sql = "\nUNION ALL\n".join(union_parts)

    unmapped_df = con.execute(f"""
        SELECT
            DOMAIN,
            missing_what,
            code,
            label,
            COUNT(*) AS n
        FROM (
            {union_sql}
        ) u
        GROUP BY 1,2,3,4
        ORDER BY n DESC
        LIMIT 200
    """).fetchdf()

    unmapped_df.to_csv(UNMAPPED_CSV, index=False)

    # ------------------------------------------------------------
    # 5.3 Timing Plausibility (Stop>=Start + Missing Stop Rate)
    # ------------------------------------------------------------
    for domain, table, start_col, end_col in TIMING_SPECS:
        n = scalar_int(con, f"SELECT COUNT(*) FROM {table}")

        missing_end = scalar_int(con, f"SELECT COUNT(*) FROM {table} WHERE {end_col} IS NULL")
        missing_end_pct = round((missing_end / n) * 100, 2) if n else 0.0

        checked = scalar_int(con, f"""
            SELECT COUNT(*)
            FROM {table}
            WHERE {end_col} IS NOT NULL
              AND TRY_CAST({start_col} AS TIMESTAMP) IS NOT NULL
              AND TRY_CAST({end_col}   AS TIMESTAMP) IS NOT NULL
        """)

        ok = scalar_int(con, f"""
            SELECT COUNT(*)
            FROM {table}
            WHERE {end_col} IS NOT NULL
              AND TRY_CAST({start_col} AS TIMESTAMP) IS NOT NULL
              AND TRY_CAST({end_col}   AS TIMESTAMP) IS NOT NULL
              AND TRY_CAST({end_col}   AS TIMESTAMP) >= TRY_CAST({start_col} AS TIMESTAMP)
        """)

        bad = scalar_int(con, f"""
            SELECT COUNT(*)
            FROM {table}
            WHERE {end_col} IS NOT NULL
              AND TRY_CAST({start_col} AS TIMESTAMP) IS NOT NULL
              AND TRY_CAST({end_col}   AS TIMESTAMP) IS NOT NULL
              AND TRY_CAST({end_col}   AS TIMESTAMP) < TRY_CAST({start_col} AS TIMESTAMP)
        """)

        ok_rate_pct = round((ok / checked) * 100, 2) if checked else 100.0

        timing_rows.append({
            "DOMAIN": domain,
            "N": n,
            "end_missing_pct": missing_end_pct,
            "checked_end_notnull": int(checked),
            "ok_end_ge_start": int(ok),
            "bad_end_lt_start": int(bad),
            "ok_rate_pct": ok_rate_pct,
        })

        # Hard failure (optional): any bad ordering
        if bad > 0:
            hard_failures.append(f"{table}: {end_col} < {start_col} rows={bad}")

    timing_df = pd.DataFrame(timing_rows)
    timing_df.to_csv(TIMING_CSV, index=False)

    # ------------------------------------------------------------
    # 5.4 EXTRA QC A: SDTM RI to DM (USUBJID must exist in DM)
    # ------------------------------------------------------------
    for domain, table in RI_TABLES:
        n = scalar_int(con, f"SELECT COUNT(*) FROM {table}")

        # missing usubjid itself
        missing_usubjid = scalar_int(con, f"""
            SELECT COUNT(*)
            FROM {table}
            WHERE USUBJID IS NULL OR TRIM(CAST(USUBJID AS VARCHAR)) = ''
        """)

        # FK missing (USUBJID not found in DM)
        fk_missing = scalar_int(con, f"""
            SELECT COUNT(*)
            FROM {table} x
            LEFT JOIN sdtm_dm d ON x.USUBJID = d.USUBJID
            WHERE x.USUBJID IS NOT NULL
              AND TRIM(CAST(x.USUBJID AS VARCHAR)) <> ''
              AND d.USUBJID IS NULL
        """)

        ri_rows.append({
            "DOMAIN": domain,
            "N": n,
            "usubjid_missing_n": int(missing_usubjid),
            "usubjid_fk_missing_n": int(fk_missing),
            "usubjid_ok_rate_pct": round((1 - ((missing_usubjid + fk_missing) / n)) * 100, 2) if n else 100.0,
        })

        if fk_missing > 0:
            hard_failures.append(f"{table}: USUBJID not in DM rows={fk_missing}")

    ri_df = pd.DataFrame(ri_rows)
    ri_df.to_csv(RI_TO_DM_CSV, index=False)

    # ------------------------------------------------------------
    # 5.5 EXTRA QC B: Duplicate group heuristics
    # ------------------------------------------------------------
    for domain, table, key_expr in DUP_SPECS:
        n_groups = scalar_int(con, f"""
            SELECT COUNT(*) FROM (
              SELECT {key_expr}
              FROM {table}
              GROUP BY {key_expr}
              HAVING COUNT(*) > 1
            ) g
        """)

        dup_rows.append({
            "DOMAIN": domain,
            "table": table,
            "dup_groups": int(n_groups),
            "key": key_expr,
        })

    dup_df = pd.DataFrame(dup_rows)
    dup_df.to_csv(DUP_GROUPS_CSV, index=False)

finally:
    con.close()

# ============================================================
# 6) Summary JSON 
# ============================================================

worst_code = (
    coverage_df["code_present_pct"].dropna().min()
    if coverage_df["code_present_pct"].notna().any()
    else None
)
worst_label = (
    coverage_df["label_present_pct"].dropna().min()
    if coverage_df["label_present_pct"].notna().any()
    else None
)
any_bad_timing = int((timing_df["bad_end_lt_start"] > 0).any()) if not timing_df.empty else 0
any_ri_fk_missing = int((ri_df["usubjid_fk_missing_n"] > 0).any()) if not ri_df.empty else 0
any_dup_groups = int((dup_df["dup_groups"] > 0).any()) if not dup_df.empty else 0

summary = {
    "run_id": run_id,
    "checked_at_utc": CHECKED_AT.isoformat(),
    "reports": {
        "coverage_csv": COVERAGE_CSV.as_posix(),
        "top_unmapped_csv": UNMAPPED_CSV.as_posix(),
        "timing_csv": TIMING_CSV.as_posix(),
        "ri_to_dm_csv": RI_TO_DM_CSV.as_posix(),
        "dup_groups_csv": DUP_GROUPS_CSV.as_posix(),
        "html_report": REPORT_HTML.as_posix(),
        "chart_coverage_png": CHART_COVERAGE_PNG.as_posix(),
        "chart_timing_bad_png": CHART_TIMING_BAD_PNG.as_posix(),
        "chart_timing_missing_end_png": CHART_TIMING_MISSING_END_PNG.as_posix(),
        "chart_ri_missing_png": CHART_RI_MISSING_PNG.as_posix(),
    },
    "summary": {
        "worst_code_present_pct": worst_code,
        "worst_label_present_pct": worst_label,
        "any_bad_end_lt_start": any_bad_timing,
        "any_ri_fk_missing": any_ri_fk_missing,
        "any_dup_groups": any_dup_groups,
        "hard_fail_count": len(hard_failures),
    },
    "hard_failures": hard_failures[:100],
}
SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

# ============================================================
# 7) Grafik Outputs (PNG)
# ============================================================

# --- Coverage chart ---
plt.figure()

domains = coverage_df["DOMAIN"].astype(str).tolist()
code_vals = coverage_df["code_present_pct"].tolist()
label_vals = coverage_df["label_present_pct"].tolist()

code_mask = [v is not None and pd.notna(v) for v in code_vals]
label_mask = [v is not None and pd.notna(v) for v in label_vals]

idx = list(range(len(domains)))
bar_w = 0.4

code_x = [i - bar_w/2 for i, ok in zip(idx, code_mask) if ok]
code_y = [v for v, ok in zip(code_vals, code_mask) if ok]
plt.bar(code_x, code_y, width=bar_w, label="code_present_pct")

label_x = [i + bar_w/2 for i, ok in zip(idx, label_mask) if ok]
label_y = [v for v, ok in zip(label_vals, label_mask) if ok]
plt.bar(label_x, label_y, width=bar_w, label="label_present_pct")

plt.xticks(idx, domains)
plt.ylabel("Percent")
plt.title("SDTM Mapping Coverage (Code/Label)")
plt.ylim(0, 105)
plt.legend()
plt.tight_layout()

for i in idx:
    if not code_mask[i]:
        plt.text(i - bar_w/2, 2, "n/a", ha="center", va="bottom", fontsize=9)
    if not label_mask[i]:
        plt.text(i + bar_w/2, 2, "n/a", ha="center", va="bottom", fontsize=9)

plt.savefig(CHART_COVERAGE_PNG)
plt.close()

# --- Timing bad counts chart ---
plt.figure()

timing_x = timing_df["DOMAIN"].astype(str).tolist()
timing_bad = timing_df["bad_end_lt_start"].fillna(0).astype(int).tolist()

plt.bar(timing_x, timing_bad)
plt.ylabel("bad_end_lt_start (count)")
plt.title("Timing Plausibility: End < Start (Counts)")

ymax = max(timing_bad) if timing_bad else 0
plt.ylim(0, max(1, ymax))
for x, v in zip(timing_x, timing_bad):
    plt.text(x, v + 0.02, str(v), ha="center", va="bottom", fontsize=9)

plt.tight_layout()
plt.savefig(CHART_TIMING_BAD_PNG)
plt.close()

# --- Timing missing end % chart ---
plt.figure()

missing_end_pct = timing_df["end_missing_pct"].fillna(0).tolist()
plt.bar(timing_x, missing_end_pct)
plt.ylabel("end_missing_pct")
plt.title("Timing Completeness: Missing End Date (%)")

ymax = max(missing_end_pct) if missing_end_pct else 0
plt.ylim(0, max(1, ymax))
for x, v in zip(timing_x, missing_end_pct):
    plt.text(x, v + 0.02, f"{v:.2f}", ha="center", va="bottom", fontsize=9)

plt.tight_layout()
plt.savefig(CHART_TIMING_MISSING_END_PNG)
plt.close()

# --- RI missing chart (stacked counts) ---
plt.figure()

ri_x = ri_df["DOMAIN"].astype(str).tolist()
miss_self = ri_df["usubjid_missing_n"].fillna(0).astype(int).tolist()
miss_fk = ri_df["usubjid_fk_missing_n"].fillna(0).astype(int).tolist()

plt.bar(ri_x, miss_self, label="USUBJID missing (null/empty)")
plt.bar(ri_x, miss_fk, bottom=miss_self, label="USUBJID not in DM")
plt.ylabel("Count")
plt.title("SDTM RI to DM: Missing/Invalid USUBJID (Counts)")

ymax = max([a + b for a, b in zip(miss_self, miss_fk)]) if ri_x else 0
plt.ylim(0, max(1, ymax))
for x, a, b in zip(ri_x, miss_self, miss_fk):
    plt.text(x, a + b + 0.02, str(a + b), ha="center", va="bottom", fontsize=9)

plt.legend()
plt.tight_layout()
plt.savefig(CHART_RI_MISSING_PNG)
plt.close()

# ============================================================
# 8) HTML Report
# ============================================================

css = """
<style>
  body { font-family: Arial, sans-serif; margin: 24px; }
  h1,h2,h3 { margin-bottom: 8px; }
  .meta { color: #333; margin-bottom: 16px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0 24px 0; }
  th, td { border: 1px solid #ddd; padding: 8px; font-size: 13px; vertical-align: top; }
  th { background: #f3f3f3; }
  .ok { background: #eef9ee; }
  .fail { background: #fdeaea; }
  code { background: #f7f7f7; padding: 2px 4px; border-radius: 3px; }
  .small { font-size: 12px; color: #555; }
  img { max-width: 100%; height: auto; border: 1px solid #eee; }
</style>
"""

coverage_preview_rows = [
    (
        r["DOMAIN"],
        r["N"],
        r["code_present_pct"],
        r["code_missing_n"],
        r["label_present_pct"],
        r["label_missing_n"],
    )
    for r in coverage_rows
]

unmapped_preview_rows: List[Tuple] = []
if unmapped_df.empty:
    unmapped_preview_rows = [("—", "—", "—", "—", "no unmapped rows")]
else:
    for _, r in unmapped_df.head(15).iterrows():
        unmapped_preview_rows.append((
            r.get("DOMAIN"),
            r.get("missing_what"),
            r.get("code"),
            r.get("label"),
            int(r.get("n")),
        ))

timing_preview_rows = [
    (
        r["DOMAIN"],
        r["N"],
        r["end_missing_pct"],
        r["checked_end_notnull"],
        r["bad_end_lt_start"],
        r["ok_rate_pct"],
    )
    for r in timing_rows
]

ri_preview_rows = [
    (r["DOMAIN"], r["N"], r["usubjid_missing_n"], r["usubjid_fk_missing_n"], r["usubjid_ok_rate_pct"])
    for r in ri_rows
]

dup_preview_rows = [
    (r["DOMAIN"], r["table"], r["dup_groups"], r["key"])
    for r in dup_rows
]

fail_list_html = "<li class='ok'>None</li>"
if hard_failures:
    fail_list_html = "".join([f"<li class='fail'>{escape(f)}</li>" for f in hard_failures[:50]])

html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>SDTM Mapping QC Report</title>
{css}
</head>
<body>

<h1>STEP 07 – SDTM Mapping QC Report</h1>

<div class="meta">
  <div><strong>run_id:</strong> <code>{escape(str(run_id))}</code></div>
  <div><strong>checked_at (UTC):</strong> {escape(CHECKED_AT.isoformat())}</div>
  <div><strong>db:</strong> <code>{escape(DB_PATH.as_posix())}</code></div>
  <div><strong>strict_qc:</strong> {escape(str(STRICT_QC))}</div>
  <div><strong>worst_code_present_pct:</strong> {escape(str(worst_code))}</div>
  <div><strong>worst_label_present_pct:</strong> {escape(str(worst_label))}</div>
  <div><strong>hard_fail_count:</strong> {escape(str(len(hard_failures)))}</div>
</div>

<h2>Grafiken</h2>
<p class="small">PNG/HTML liegen im Ordner: <code>{escape(QC_DIR.as_posix())}</code>. HTML bitte aus diesem Ordner öffnen (relative img src).</p>

<h3>Coverage</h3>
<img src="{escape(CHART_COVERAGE_PNG.name)}" alt="Coverage Chart"/>

<h3>Timing (Bad End &lt; Start)</h3>
<img src="{escape(CHART_TIMING_BAD_PNG.name)}" alt="Timing Bad Chart"/>

<h3>Timing (Missing End %)</h3>
<img src="{escape(CHART_TIMING_MISSING_END_PNG.name)}" alt="Timing Missing End Chart"/>

<h3>RI to DM (Missing/Invalid USUBJID)</h3>
<img src="{escape(CHART_RI_MISSING_PNG.name)}" alt="RI Missing USUBJID Chart"/>

<h2>1) Coverage (Code/Label Completeness)</h2>
{html_table(
  ["DOMAIN","N","code_present_pct","code_missing_n","label_present_pct","label_missing_n"],
  coverage_preview_rows
)}

<h2>2) Top Unmapped (Preview)</h2>
<p class="small">Unmapped = Code oder Label fehlt/leer/UNKNOWN/UNK (LB/VS).</p>
{html_table(
  ["DOMAIN","missing_what","code","label","n"],
  unmapped_preview_rows
)}

<h2>3) Timing Plausibility (Stop ≥ Start)</h2>
{html_table(
  ["DOMAIN","N","end_missing_pct","checked_end_notnull","bad_end_lt_start","ok_rate_pct"],
  timing_preview_rows
)}

<h2>4) EXTRA QC A – SDTM RI to DM (USUBJID)</h2>
<p class="small">Alle Domain-Records müssen einen USUBJID haben, der in DM existiert.</p>
{html_table(
  ["DOMAIN","N","usubjid_missing_n","usubjid_fk_missing_n","usubjid_ok_rate_pct"],
  ri_preview_rows
)}

<h2>5) EXTRA QC B – Duplicate Groups (Heuristic)</h2>
<p class="small">Zählt Duplikat-Gruppen auf pragmatischen Keys (Signal für Mapping-Dedupe Bedarf).</p>
{html_table(
  ["DOMAIN","table","dup_groups","key_expr"],
  dup_preview_rows
)}

<h2>6) Hard Failures</h2>
<ul>
{fail_list_html}
</ul>

<h2>Outputs</h2>
<ul>
  <li>coverage_csv: <code>{escape(COVERAGE_CSV.as_posix())}</code></li>
  <li>top_unmapped_csv: <code>{escape(UNMAPPED_CSV.as_posix())}</code></li>
  <li>timing_csv: <code>{escape(TIMING_CSV.as_posix())}</code></li>
  <li>ri_to_dm_csv: <code>{escape(RI_TO_DM_CSV.as_posix())}</code></li>
  <li>dup_groups_csv: <code>{escape(DUP_GROUPS_CSV.as_posix())}</code></li>
  <li>summary_json: <code>{escape(SUMMARY_JSON.as_posix())}</code></li>
</ul>

</body>
</html>
"""

REPORT_HTML.write_text(html_doc, encoding="utf-8")

# ============================================================
# 9) Manifest updaten + optional Gate
# ============================================================

manifest_out = dict(manifest_in)
manifest_out.update({
    "sdtm_qc": {
        "checked_at_utc": CHECKED_AT.isoformat(),
        "strict_qc": STRICT_QC,
        "reports": {
            "coverage_csv": COVERAGE_CSV.as_posix(),
            "top_unmapped_csv": UNMAPPED_CSV.as_posix(),
            "timing_csv": TIMING_CSV.as_posix(),
            "ri_to_dm_csv": RI_TO_DM_CSV.as_posix(),
            "dup_groups_csv": DUP_GROUPS_CSV.as_posix(),
            "summary_json": SUMMARY_JSON.as_posix(),
            "html_report": REPORT_HTML.as_posix(),
            "chart_coverage_png": CHART_COVERAGE_PNG.as_posix(),
            "chart_timing_bad_png": CHART_TIMING_BAD_PNG.as_posix(),
            "chart_timing_missing_end_png": CHART_TIMING_MISSING_END_PNG.as_posix(),
            "chart_ri_missing_png": CHART_RI_MISSING_PNG.as_posix(),
        },
        "worst_code_present_pct": worst_code,
        "worst_label_present_pct": worst_label,
        "any_bad_end_lt_start": int(any_bad_timing),
        "any_ri_fk_missing": int(any_ri_fk_missing),
        "any_dup_groups": int(any_dup_groups),
        "hard_fail_count": int(len(hard_failures)),
        "hard_failures": hard_failures[:100],
    }
})

MANIFEST_OUT_PATH.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

print("\n" + "-" * 70)
print("SDTM MAPPING QC - SUMMARY")
print("-" * 70)
print(f"[out] coverage csv        : {COVERAGE_CSV}")
print(f"[out] unmapped csv        : {UNMAPPED_CSV}")
print(f"[out] timing csv          : {TIMING_CSV}")
print(f"[out] ri_to_dm csv        : {RI_TO_DM_CSV}")
print(f"[out] dup_groups csv      : {DUP_GROUPS_CSV}")
print(f"[out] summary json        : {SUMMARY_JSON}")
print(f"[out] html report         : {REPORT_HTML}")
print(f"[out] chart coverage png  : {CHART_COVERAGE_PNG}")
print(f"[out] chart timing bad png: {CHART_TIMING_BAD_PNG}")
print(f"[out] chart timing miss % : {CHART_TIMING_MISSING_END_PNG}")
print(f"[out] chart RI missing    : {CHART_RI_MISSING_PNG}")
print(f"[dq ] worst_code_present% : {worst_code if worst_code is not None else 'n/a'}")
print(f"[dq ] worst_label_present%: {worst_label if worst_label is not None else 'n/a'}")
print(f"[dq ] any bad_end_lt_start: {int(any_bad_timing)}")
print(f"[dq ] any RI fk missing   : {int(any_ri_fk_missing)}")
print(f"[dq ] any dup groups      : {int(any_dup_groups)}")
print(f"[dq ] hard_fail_count     : {len(hard_failures)}")
print(f"[out] manifest            : {MANIFEST_OUT_PATH}")

if STRICT_QC and hard_failures:
    print("\n[dq ] QUALITY GATE FAILED (STRICT_QC=True)")
    raise SystemExit(2)

print("=" * 70 + "\n")
