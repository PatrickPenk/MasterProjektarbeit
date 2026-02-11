# ============================================================
# 10_marts_build.py
# ============================================================

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Dict, List, Any

import duckdb
import pandas as pd
import numpy as np

from scripts.config import DB_PATH, MANIFEST_DIR, MARTS_DIR, ensure_dirs

ensure_dirs()
MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
MARTS_DIR.mkdir(parents=True, exist_ok=True)

print("\n" + "=" * 70)
print("STEP 10 – MARTS (V3 Prep: Mart_01..Mart_10) → DuckDB/CSV/Manifest")
print("=" * 70)

# ============================================================
# 0) Policy / Feature QC / Missingness Handling
# ============================================================

# Coverage-Schwellen für LAB Features (global)
MIN_LAB_NON_MISSING_RATE = 0.01   # 1% Coverage
MIN_LAB_NON_MISSING_COUNT = 20    # oder mind. 20 Beobachtungen insgesamt

# Optional: final merged numeric NA -> 0 (sehr aggressiv; meist False lassen!)
FILL_REMAINING_NUMERIC_NA_WITH_ZERO = False

# ============================================================
# 1) Manifest laden (Input aus Step 09 / Step 08)
# ============================================================

manifest_candidates = [
    MANIFEST_DIR / "manifest_adam_checked.json",
    MANIFEST_DIR / "manifest_adam.json",
    MANIFEST_DIR / "manifest_sdtm_checked.json",
    MANIFEST_DIR / "manifest_sdtm.json",
]

manifest_in_path = next((p for p in manifest_candidates if p.exists()), None)
if manifest_in_path is None:
    raise FileNotFoundError(
        "Kein Manifest gefunden. Erwartet:\n"
        f" - {manifest_candidates[0]}\n"
        f" - {manifest_candidates[1]}\n"
        f" - {manifest_candidates[2]}\n"
        f" - {manifest_candidates[3]}\n"
        "Bitte zuerst Step 06 + Step 08 (+ Step 09) ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
run_id = (
    manifest_in.get("run_id")
    or manifest_in.get("adam_qc", {}).get("run_id")
    or manifest_in.get("sdtm_qc", {}).get("run_id")
)
if not run_id:
    raise ValueError("run_id fehlt im Manifest. Bitte frühere Steps erneut ausführen.")

RUN_TS = datetime.now(timezone.utc)

print(f"[in ] manifest            : {manifest_in_path}")
print(f"[run] run_id              : {run_id}")
print(f"[db ] path                : {DB_PATH}")

# ============================================================
# 2) Output dirs / files
# ============================================================

MART_DIR = MARTS_DIR
MART_DIR.mkdir(parents=True, exist_ok=True)

CSV_WIDE = MART_DIR / "mart_wide_from_sv.csv"
CSV_WIDE_ALL_VISITS = MART_DIR / "mart_wide_all_visits.csv"
CSV_WIDE_VISITS_CAT = MART_DIR / "mart_wide_visits_cat.csv"
CSV_WIDE_AGG = MART_DIR / "mart_wide_aggregation.csv"
CSV_LAB_VALUES = MART_DIR / "mart_lab_values.csv"
CSV_ADSL_DEMO = MART_DIR / "mart_adsl_demo.csv"
CSV_MERGED = MART_DIR / "mart_merged_for_lm.csv"

# NEW: Feature QC reports
CSV_LAB_FEATURE_COVERAGE = MART_DIR / "lab_feature_coverage.csv"
CSV_LAB_FEATURE_DROPPED = MART_DIR / "lab_features_dropped.csv"

MANIFEST_OUT = MANIFEST_DIR / "manifest_marts.json"

# ============================================================
# 3) Helpers
# ============================================================

MISSING_TOKENS = {"", "nan", "none", "null", "na", "n/a", "nat"}

def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path))
    con.execute("PRAGMA threads=4")
    return con

def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ? LIMIT 1",
        [name],
    ).fetchone() is not None

def safe_write_csv(con: duckdb.DuckDBPyConnection, table: str, out_path: Path) -> None:
    if table_exists(con, table):
        con.execute(f"COPY {table} TO ? (HEADER, DELIMITER ',')", [str(out_path)])

def _safe_col(s: str) -> str:
    s = str(s)
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def _coerce_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=False)

def normalize_missing_tokens(df: pd.DataFrame) -> pd.DataFrame:
    """typische Missing-Strings in object Spalten -> pd.NA"""
    if df is None or df.empty:
        return df
    out = df.copy()
    obj_cols = [c for c in out.columns if out[c].dtype == "object"]
    for c in obj_cols:
        s = out[c].astype(str)
        s2 = s.str.strip()
        mask = s2.str.lower().isin(MISSING_TOKENS)
        out.loc[mask, c] = pd.NA
        out.loc[s2.eq(""), c] = pd.NA
    return out

def replace_inf_with_na(df: pd.DataFrame) -> pd.DataFrame:
    """inf/-inf in numeric Spalten -> NA"""
    if df is None or df.empty:
        return df
    out = df.copy()
    num_cols = [c for c in out.columns if pd.api.types.is_numeric_dtype(out[c])]
    for c in num_cols:
        s = pd.to_numeric(out[c], errors="coerce")
        s = s.replace([np.inf, -np.inf], np.nan)
        out[c] = s
    return out

def ensure_usubjid_str(df: pd.DataFrame) -> pd.DataFrame:
    """USUBJID als string, leere/Token -> NA"""
    if df is None or df.empty:
        return df
    if "USUBJID" in df.columns:
        df = df.copy()
        df["USUBJID"] = df["USUBJID"].astype(str).str.strip()
        df.loc[df["USUBJID"].str.lower().isin(MISSING_TOKENS), "USUBJID"] = pd.NA
        df.loc[df["USUBJID"].eq(""), "USUBJID"] = pd.NA
    return df

def compute_feature_coverage(df: pd.DataFrame, id_col: str = "USUBJID") -> pd.DataFrame:
    """Coverage je Feature-Spalte: non_missing_count, non_missing_rate (bezogen auf unique IDs)"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["feature", "non_missing_count", "non_missing_rate"])
    if id_col not in df.columns:
        raise RuntimeError(f"Coverage: id_col '{id_col}' fehlt.")
    n = int(df[id_col].nunique(dropna=True))
    features = [c for c in df.columns if c != id_col]
    rows = []
    for c in features:
        non_missing = int(df[c].notna().sum())
        rate = (non_missing / n) if n else 0.0
        rows.append({"feature": c, "non_missing_count": non_missing, "non_missing_rate": float(rate)})
    return pd.DataFrame(rows).sort_values(["non_missing_rate", "non_missing_count"], ascending=[False, False])

def drop_features_by_coverage(
    df: pd.DataFrame,
    coverage_df: pd.DataFrame,
    min_rate: float,
    min_count: int,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Drop Features, wenn rate < min_rate UND count < min_count"""
    if df is None or df.empty:
        return df, pd.DataFrame(columns=["feature", "reason"])
    cov = coverage_df.copy()
    cov["drop"] = (cov["non_missing_rate"] < min_rate) & (cov["non_missing_count"] < min_count)
    to_drop = cov.loc[cov["drop"], "feature"].tolist()
    dropped = cov.loc[cov["drop"], ["feature", "non_missing_count", "non_missing_rate"]].copy()
    dropped["reason"] = f"coverage_below(min_rate={min_rate}, min_count={min_count})"
    out = df.drop(columns=to_drop, errors="ignore")
    return out, dropped

def fill_lab_means_with_median(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, float], List[str]]:
    """
    LAB_*_MEAN:
      - komplett NA -> drop (damit ML keine "no observed values" Warnung bekommt)
      - sonst NA -> Median (Fallback 0.0)
    LAB_*_count:
      - NA -> 0, int
    """
    if df is None or df.empty:
        return df, {}, []
    out = df.copy()
    medians: Dict[str, float] = {}
    dropped_all_missing: List[str] = []

    mean_cols = [c for c in out.columns if c.startswith("LAB_") and c.endswith("_MEAN")]
    count_cols = [c for c in out.columns if c.startswith("LAB_") and c.endswith("_count")]

    for c in count_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype("int64")

    for c in mean_cols:
        s = pd.to_numeric(out[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if int(s.notna().sum()) == 0:
            dropped_all_missing.append(c)
            continue
        med = float(s.median(skipna=True))
        if pd.isna(med):
            med = 0.0
        medians[c] = med
        out[c] = s.fillna(med)

    if dropped_all_missing:
        out = out.drop(columns=dropped_all_missing, errors="ignore")

    return out, medians, dropped_all_missing

def count_numeric_na_cells(df: pd.DataFrame, id_col: str = "USUBJID") -> int:
    if df is None or df.empty:
        return 0
    num_cols = [c for c in df.columns if c != id_col and pd.api.types.is_numeric_dtype(df[c])]
    if not num_cols:
        return 0
    return int(df[num_cols].isna().sum().sum())

def count_bad_object_tokens(df: pd.DataFrame) -> int:
    """
    Zählt "bad tokens" in object Spalten (Strings wie 'nan', 'none', 'null', '' ...)
    """
    if df is None or df.empty:
        return 0
    obj_cols = [c for c in df.columns if df[c].dtype == "object"]
    if not obj_cols:
        return 0
    bad = 0
    bad_set = set(MISSING_TOKENS)
    for c in obj_cols:
        s = df[c].astype(str).str.strip().str.lower()
        bad += int(s.isin(bad_set).sum())
    return int(bad)

# ============================================================
# 4) Preconditions: benötigte Tabellen prüfen & laden
# ============================================================

con = connect(DB_PATH)

try:
    required = ["adam_adsl", "sdtm_sv"]
    missing = [t for t in required if not table_exists(con, t)]
    if missing:
        raise RuntimeError(
            "Fehlende Tabellen für MARTS.\n"
            f"Required: {required}\n"
            f"Missing : {missing}\n"
            "Bitte Step 06 (SDTM) + Step 08 (ADaM) ausführen."
        )

    has_lb = table_exists(con, "sdtm_lb")

    adsl = con.execute("SELECT * FROM adam_adsl").fetchdf()
    sv = con.execute("SELECT * FROM sdtm_sv").fetchdf()
    lb = con.execute("SELECT * FROM sdtm_lb").fetchdf() if has_lb else pd.DataFrame()

    # global cleanup (tokens + keys + inf)
    adsl = replace_inf_with_na(normalize_missing_tokens(ensure_usubjid_str(adsl)))
    sv   = replace_inf_with_na(normalize_missing_tokens(ensure_usubjid_str(sv)))
    lb   = replace_inf_with_na(normalize_missing_tokens(ensure_usubjid_str(lb)))

    # ============================================================
    # 5) wide aus SV erzeugen
    # ============================================================

    MAX_VISITS = 20

    sv_cols = set(sv.columns)
    if "VISITNUM" not in sv_cols:
        raise RuntimeError("sdtm_sv hat keine VISITNUM – kann wide nicht deterministisch bauen.")
    if "VISIT" not in sv_cols:
        sv["VISIT"] = pd.NA

    if "SVSTDTC" in sv_cols:
        sv["SVSTDTC_TS"] = _coerce_ts(sv["SVSTDTC"])
    else:
        sv["SVSTDTC_TS"] = pd.NaT

    if "SVENDTC" in sv_cols:
        sv["SVENDTC_TS"] = _coerce_ts(sv["SVENDTC"])
    else:
        sv["SVENDTC_TS"] = pd.NaT

    sv["VISITNUM_NUM"] = pd.to_numeric(sv["VISITNUM"], errors="coerce")

    sv_sorted = sv.sort_values(
        ["USUBJID", "SVSTDTC_TS", "VISITNUM_NUM"],
        ascending=[True, True, True],
        na_position="last",
    ).copy()

    sv_sorted = sv_sorted.dropna(subset=["USUBJID"]).copy()

    sv_sorted["SEQ"] = sv_sorted.groupby("USUBJID").cumcount() + 1
    sv_sorted = sv_sorted[sv_sorted["SEQ"] <= MAX_VISITS].copy()

    wide_visitnum = sv_sorted.pivot(index="USUBJID", columns="SEQ", values="VISITNUM_NUM")
    wide_start = sv_sorted.pivot(index="USUBJID", columns="SEQ", values="SVSTDTC_TS")
    wide_end = sv_sorted.pivot(index="USUBJID", columns="SEQ", values="SVENDTC_TS")

    wide = pd.DataFrame({"USUBJID": wide_visitnum.index}).reset_index(drop=True)

    for i in range(1, MAX_VISITS + 1):
        if i in wide_visitnum.columns:
            wide[f"VISITID_{i}"] = wide_visitnum[i].values
        if i in wide_start.columns:
            wide[f"START_{i}"] = wide_start[i].values
        if i in wide_end.columns:
            wide[f"ENDE_{i}"] = wide_end[i].values

    con.execute("DROP TABLE IF EXISTS mart_wide")
    con.register("wide_df", wide)
    con.execute("CREATE TABLE mart_wide AS SELECT * FROM wide_df")
    con.unregister("wide_df")

    # ============================================================
    # 6) Mart_01 – wide_all_visits
    # ============================================================

    def make_wide_all_visits_keep_visitid_colnames(
        wide: pd.DataFrame,
        max_visits: int = 20,
        clamp_negative_to_zero: bool = True,
    ) -> pd.DataFrame:
        out = wide.copy()

        for i in range(1, max_visits + 1):
            vid = f"VISITID_{i}"
            s = f"START_{i}"
            e = f"ENDE_{i}"
            los_col = f"LOS_{i}"

            if not all(c in out.columns for c in [vid, s, e]):
                continue

            out[s] = pd.to_datetime(out[s], errors="coerce")
            out[e] = pd.to_datetime(out[e], errors="coerce")

            delta = out[e] - out[s]
            los = delta.dt.total_seconds() / 60

            if clamp_negative_to_zero:
                los = los.where(los.isna() | (los >= 0), 0)

            out[los_col] = los
            out[vid] = los

        drop_cols = [
            c
            for i in range(1, max_visits + 1)
            for c in (f"START_{i}", f"ENDE_{i}")
            if c in out.columns
        ]
        out = out.drop(columns=drop_cols)

        keep = ["USUBJID"] + [
            f"VISITID_{i}" for i in range(1, max_visits + 1) if f"VISITID_{i}" in out.columns
        ]
        out = out[keep]
        return out

    wide_all_visits = make_wide_all_visits_keep_visitid_colnames(wide, max_visits=MAX_VISITS)

    con.execute("DROP TABLE IF EXISTS mart_wide_all_visits")
    con.register("wide_all_visits_df", wide_all_visits)
    con.execute("CREATE TABLE mart_wide_all_visits AS SELECT * FROM wide_all_visits_df")
    con.unregister("wide_all_visits_df")

    # ============================================================
    # 7) Mart_02 – wide_visits_cat
    # ============================================================

    def add_visit_categories_next_to_los(
        wide: pd.DataFrame,
        wide_all_visits: pd.DataFrame,
        sv: pd.DataFrame,
        max_visits: int = 20,
    ) -> pd.DataFrame:
        sv_lu = (
            sv[["USUBJID", "VISITNUM", "VISIT"]]
            .dropna(subset=["USUBJID", "VISITNUM"])
            .copy()
        )
        sv_lu["VISITNUM"] = pd.to_numeric(sv_lu["VISITNUM"], errors="coerce")
        sv_lu = (
            sv_lu.dropna(subset=["VISITNUM"])
                .sort_values(["USUBJID", "VISITNUM", "VISIT"])
                .drop_duplicates(["USUBJID", "VISITNUM"], keep="first")
        )

        visitid_cols = [f"VISITID_{i}" for i in range(1, max_visits + 1) if f"VISITID_{i}" in wide.columns]
        long = wide[["USUBJID"] + visitid_cols].copy()
        long = long.melt(id_vars="USUBJID", var_name="seq_col", value_name="VISITNUM")
        long["seq"] = long["seq_col"].str.extract(r"(\d+)$").astype(int)
        long = long.dropna(subset=["VISITNUM"]).copy()
        long["VISITNUM"] = pd.to_numeric(long["VISITNUM"], errors="coerce")
        long = long.dropna(subset=["VISITNUM"]).copy()

        long = long.merge(
            sv_lu, left_on=["USUBJID", "VISITNUM"], right_on=["USUBJID", "VISITNUM"], how="left"
        )

        visit_wide = long.pivot(index="USUBJID", columns="seq", values="VISIT")
        visit_wide.columns = [f"VISIT_{i}" for i in visit_wide.columns]
        visit_wide = visit_wide.reset_index()

        merged = wide_all_visits.merge(visit_wide, on="USUBJID", how="left")

        ordered = ["USUBJID"]
        for i in range(1, max_visits + 1):
            los_col = f"VISITID_{i}"
            cat_col = f"VISIT_{i}"
            if los_col in merged.columns:
                ordered.append(los_col)
            if cat_col in merged.columns:
                ordered.append(cat_col)

        wide_visits_cat = merged[ordered].copy()
        return wide_visits_cat

    wide_visits_cat = add_visit_categories_next_to_los(wide, wide_all_visits, sv, max_visits=MAX_VISITS)

    con.execute("DROP TABLE IF EXISTS mart_wide_visits_cat")
    con.register("wide_visits_cat_df", wide_visits_cat)
    con.execute("CREATE TABLE mart_wide_visits_cat AS SELECT * FROM wide_visits_cat_df")
    con.unregister("wide_visits_cat_df")

    # ============================================================
    # 8) Mart_03 – wide_aggregation
    # ============================================================

    def build_target_and_features_from_wide_visits_cat(
        wide_visits_cat: pd.DataFrame,
        max_visits: int = 20,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        df = wide_visits_cat.copy()

        long_parts = []
        for i in range(1, max_visits + 1):
            los_col = f"VISITID_{i}"
            cat_col = f"VISIT_{i}"
            if los_col in df.columns:
                tmp = df[["USUBJID", los_col]].copy()
                tmp = tmp.rename(columns={los_col: "LOS"})
                tmp["CATEGORY"] = df[cat_col] if cat_col in df.columns else pd.NA
                tmp["SEQ"] = i
                long_parts.append(tmp)

        long = pd.concat(long_parts, ignore_index=True)
        long = long.dropna(subset=["USUBJID"]).copy()
        long["LOS"] = pd.to_numeric(long["LOS"], errors="coerce").replace([np.inf, -np.inf], np.nan)
        long = long.dropna(subset=["LOS"]).copy()

        last_seq = long.groupby("USUBJID")["SEQ"].max().reset_index(name="LAST_SEQ")

        last_rows = long.merge(last_seq, on="USUBJID", how="inner")
        last_rows = last_rows[last_rows["SEQ"] == last_rows["LAST_SEQ"]].copy()

        target = last_rows[["USUBJID", "LOS", "CATEGORY"]].rename(
            columns={"LOS": "TARGET_LOS_LAST", "CATEGORY": "TARGET_LAST_VISIT_CAT"}
        )

        feat_long = long.merge(last_seq, on="USUBJID", how="inner")
        feat_long = feat_long[feat_long["SEQ"] < feat_long["LAST_SEQ"]].copy()

        base_feat = (
            feat_long.groupby("USUBJID")["LOS"]
            .agg(
                N_VISITS_PREV="count",
                LOS_MEAN_PREV="mean",
                LOS_MAX_PREV="max",
            )
            .reset_index()
        )

        if len(feat_long) > 0:
            q = feat_long.groupby("USUBJID")["LOS"].quantile([0.25, 0.75]).unstack()
            q["LOS_IQR_PREV"] = q[0.75] - q[0.25]
            q = q[["LOS_IQR_PREV"]].reset_index()
            base_feat = base_feat.merge(q, on="USUBJID", how="left")
        else:
            base_feat["LOS_IQR_PREV"] = np.nan

        categories = (
            feat_long["CATEGORY"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        cat_feat = base_feat.copy()
        for cat in categories:
            cat_df = feat_long[feat_long["CATEGORY"] == cat]
            tmp = (
                cat_df.groupby("USUBJID")["LOS"]
                .agg(
                    **{
                        f"N_VISITS_PREV_{cat}": "count",
                        f"LOS_MEAN_PREV_{cat}": "mean",
                        f"LOS_MAX_PREV_{cat}": "max",
                    }
                )
                .reset_index()
            )
            cat_feat = cat_feat.merge(tmp, on="USUBJID", how="left")

        wide_aggregation = target.merge(cat_feat, on="USUBJID", how="left")

        for c in wide_aggregation.columns:
            if any(c.startswith(p) for p in ["LOS_MEAN_PREV_", "LOS_MAX_PREV_"]):
                wide_aggregation[c] = wide_aggregation[c].fillna(0)

        for c in wide_aggregation.columns:
            if c.startswith("N_VISITS_PREV_"):
                wide_aggregation[c] = wide_aggregation[c].fillna(0).astype(int)

        if "N_VISITS_PREV" in wide_aggregation.columns:
            wide_aggregation["N_VISITS_PREV"] = wide_aggregation["N_VISITS_PREV"].fillna(0).astype(int)

        wide_aggregation = replace_inf_with_na(wide_aggregation)
        return wide_aggregation, long

    wide_aggregation, long_df = build_target_and_features_from_wide_visits_cat(wide_visits_cat, max_visits=MAX_VISITS)

    con.execute("DROP TABLE IF EXISTS mart_wide_aggregation")
    con.register("wide_aggregation_df", wide_aggregation)
    con.execute("CREATE TABLE mart_wide_aggregation AS SELECT * FROM wide_aggregation_df")
    con.unregister("wide_aggregation_df")

    # ============================================================
    # 9) Mart_04..07 – lab_values (Coverage Filter + Median Fill)
    # ============================================================

    lab_coverage_df = pd.DataFrame()
    lab_dropped_df = pd.DataFrame()
    lab_medians: Dict[str, float] = {}
    lab_dropped_all_missing: List[str] = []

    if has_lb and not lb.empty and "LBTESTCD" in lb.columns:
        lb2 = lb.copy()

        if "LBSTRESN" in lb2.columns:
            lb2["LBSTRESN"] = pd.to_numeric(lb2["LBSTRESN"], errors="coerce").replace([np.inf, -np.inf], np.nan)
        else:
            lb2["LBSTRESN"] = np.nan

        lb2 = lb2.dropna(subset=["USUBJID"]).copy()

        lb2["HAS_NUMERIC_VALUE"] = lb2["LBSTRESN"].notna()
        lb_numeric = lb2[lb2["HAS_NUMERIC_VALUE"]].copy()

        if "LBTEST" not in lb_numeric.columns:
            lb_numeric["LBTEST"] = pd.NA

        top25_numeric = (
            lb_numeric
            .groupby(["LBTESTCD", "LBTEST"])
            .size()
            .reset_index(name="N_RECORDS")
            .sort_values("N_RECORDS", ascending=False)
            .head(25)
        )

        top25_codes = top25_numeric["LBTESTCD"].astype(str).tolist()

        lb_top = lb2.copy()
        lb_top["LBTESTCD"] = lb_top["LBTESTCD"].astype(str)
        lb_top = lb_top[lb_top["LBTESTCD"].isin(top25_codes) & lb_top["LBSTRESN"].notna()].copy()

        g = lb_top.groupby(["USUBJID", "LBTESTCD"])["LBSTRESN"]
        agg_long = (
            g.agg(
                MEAN="mean",
                count="count",
            )
            .reset_index()
        )

        lab_values = (
            agg_long
            .set_index(["USUBJID", "LBTESTCD"])
            .unstack("LBTESTCD")
        )

        lab_values.columns = [f"LAB_{_safe_col(testcd)}_{stat}" for stat, testcd in lab_values.columns]
        lab_values = lab_values.reset_index()
        lab_values = replace_inf_with_na(lab_values)

        # Coverage berechnen + exportieren
        lab_coverage_df = compute_feature_coverage(lab_values, id_col="USUBJID")
        lab_coverage_df.to_csv(CSV_LAB_FEATURE_COVERAGE, index=False)

        # Coverage Drop
        lab_values2, dropped_cov = drop_features_by_coverage(
            lab_values, lab_coverage_df, MIN_LAB_NON_MISSING_RATE, MIN_LAB_NON_MISSING_COUNT
        )

        # Median fill + drop all-missing MEAN
        lab_values3, lab_medians, lab_dropped_all_missing = fill_lab_means_with_median(lab_values2)

        # Dropped Report schreiben
        dropped_rows: List[Dict[str, Any]] = []
        if not dropped_cov.empty:
            for _, r in dropped_cov.iterrows():
                dropped_rows.append({
                    "feature": r["feature"],
                    "non_missing_count": int(r["non_missing_count"]),
                    "non_missing_rate": float(r["non_missing_rate"]),
                    "reason": str(r["reason"]),
                })
        for c in lab_dropped_all_missing:
            dropped_rows.append({
                "feature": c,
                "non_missing_count": 0,
                "non_missing_rate": 0.0,
                "reason": "all_missing_after_numeric_coercion",
            })

        lab_dropped_df = (
            pd.DataFrame(dropped_rows).sort_values(["reason", "feature"])
            if dropped_rows else pd.DataFrame()
        )
        if not lab_dropped_df.empty:
            lab_dropped_df.to_csv(CSV_LAB_FEATURE_DROPPED, index=False)

        lab_values = lab_values3

        # Ensure count cols int and NA->0
        count_cols = [c for c in lab_values.columns if c.startswith("LAB_") and c.endswith("_count")]
        for c in count_cols:
            lab_values[c] = pd.to_numeric(lab_values[c], errors="coerce").fillna(0).astype("int64")

    else:
        lab_values = pd.DataFrame({"USUBJID": wide_aggregation["USUBJID"].astype(str).unique()})

    con.execute("DROP TABLE IF EXISTS mart_lab_values")
    con.register("lab_values_df", lab_values)
    con.execute("CREATE TABLE mart_lab_values AS SELECT * FROM lab_values_df")
    con.unregister("lab_values_df")

    # ============================================================
    # 10) Mart_08 – adsl_demo
    # ============================================================

    keep_demo = [c for c in ["USUBJID", "AGE", "SEX", "RACE"] if c in adsl.columns]
    adsl_demo = adsl[keep_demo].copy()

    adsl_demo = normalize_missing_tokens(adsl_demo)
    if "AGE" in adsl_demo.columns:
        adsl_demo["AGE"] = pd.to_numeric(adsl_demo["AGE"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    for c in ["SEX", "RACE"]:
        if c in adsl_demo.columns:
            adsl_demo[c] = adsl_demo[c].fillna("UNKNOWN").astype(str)

    con.execute("DROP TABLE IF EXISTS mart_adsl_demo")
    con.register("adsl_demo_df", adsl_demo)
    con.execute("CREATE TABLE mart_adsl_demo AS SELECT * FROM adsl_demo_df")
    con.unregister("adsl_demo_df")

    # ============================================================
    # 11) Mart_09 – merged_for_LM
    # ============================================================

    con.execute("DROP TABLE IF EXISTS mart_merged_for_lm")

    q = """
    CREATE TABLE mart_merged_for_lm AS
    SELECT
      w.USUBJID,
      a.* EXCLUDE (USUBJID),
      w.* EXCLUDE (USUBJID),
      l.* EXCLUDE (USUBJID)
    FROM mart_wide_aggregation w
    LEFT JOIN mart_adsl_demo a
      ON w.USUBJID = a.USUBJID
    LEFT JOIN mart_lab_values l
      ON w.USUBJID = l.USUBJID
    """
    con.execute(q)

    # ============================================================
    # 11a) PRE/POST Missingness Tracking (numeric + object tokens)
    # ============================================================

    merged_pre = con.execute("SELECT * FROM mart_merged_for_lm").fetchdf()
    merged_pre = replace_inf_with_na(merged_pre)
    numeric_na_pre = count_numeric_na_cells(merged_pre, id_col="USUBJID")
    bad_obj_tokens_pre = count_bad_object_tokens(merged_pre)

    # OPTIONAL: aggressive global numeric NA->0
    if FILL_REMAINING_NUMERIC_NA_WITH_ZERO:
        merged_df = merged_pre.copy()
        num_cols = [c for c in merged_df.columns if c != "USUBJID" and pd.api.types.is_numeric_dtype(merged_df[c])]
        merged_df[num_cols] = merged_df[num_cols].fillna(0)
        con.execute("DROP TABLE IF EXISTS mart_merged_for_lm")
        con.register("merged_df", merged_df)
        con.execute("CREATE TABLE mart_merged_for_lm AS SELECT * FROM merged_df")
        con.unregister("merged_df")

    merged_post = con.execute("SELECT * FROM mart_merged_for_lm").fetchdf()
    merged_post = replace_inf_with_na(merged_post)
    numeric_na_post = count_numeric_na_cells(merged_post, id_col="USUBJID")
    bad_obj_tokens_post = count_bad_object_tokens(merged_post)

    numeric_na_removed = max(0, numeric_na_pre - numeric_na_post)
    bad_obj_tokens_removed = max(0, bad_obj_tokens_pre - bad_obj_tokens_post)

    # ============================================================
    # 12) Mart_10 – Key-Union Check
    # ============================================================

    q_keys = """
    WITH all_keys AS (
      SELECT USUBJID FROM mart_adsl_demo
      UNION
      SELECT USUBJID FROM mart_wide_aggregation
      UNION
      SELECT USUBJID FROM mart_lab_values
    )
    SELECT
      (SELECT COUNT(DISTINCT USUBJID) FROM all_keys)           AS union_keys,
      (SELECT COUNT(DISTINCT USUBJID) FROM mart_merged_for_lm) AS merged_keys
    """
    check_keys = con.execute(q_keys).fetchdf()

    # ============================================================
    # 13) Exporte CSV
    # ============================================================

    safe_write_csv(con, "mart_wide", CSV_WIDE)
    safe_write_csv(con, "mart_wide_all_visits", CSV_WIDE_ALL_VISITS)
    safe_write_csv(con, "mart_wide_visits_cat", CSV_WIDE_VISITS_CAT)
    safe_write_csv(con, "mart_wide_aggregation", CSV_WIDE_AGG)
    safe_write_csv(con, "mart_lab_values", CSV_LAB_VALUES)
    safe_write_csv(con, "mart_adsl_demo", CSV_ADSL_DEMO)
    safe_write_csv(con, "mart_merged_for_lm", CSV_MERGED)

    # ============================================================
    # 14) Manifest schreiben
    # ============================================================

    union_keys = int(check_keys.loc[0, "union_keys"]) if not check_keys.empty else None
    merged_keys = int(check_keys.loc[0, "merged_keys"]) if not check_keys.empty else None

    manifest_out = dict(manifest_in)
    manifest_out["marts"] = {
        "run_id": run_id,
        "built_at_utc": RUN_TS.isoformat(),
        "inputs": {
            "db_path": DB_PATH.as_posix(),
            "tables": {
                "adam_adsl": "adam_adsl",
                "sdtm_sv": "sdtm_sv",
                "sdtm_lb": "sdtm_lb" if has_lb else None,
            },
        },
        "tables": {
            "mart_wide": "mart_wide",
            "mart_wide_all_visits": "mart_wide_all_visits",
            "mart_wide_visits_cat": "mart_wide_visits_cat",
            "mart_wide_aggregation": "mart_wide_aggregation",
            "mart_lab_values": "mart_lab_values",
            "mart_adsl_demo": "mart_adsl_demo",
            "mart_merged_for_lm": "mart_merged_for_lm",
        },
        "outputs": {
            "wide_csv": CSV_WIDE.as_posix(),
            "wide_all_visits_csv": CSV_WIDE_ALL_VISITS.as_posix(),
            "wide_visits_cat_csv": CSV_WIDE_VISITS_CAT.as_posix(),
            "wide_aggregation_csv": CSV_WIDE_AGG.as_posix(),
            "lab_values_csv": CSV_LAB_VALUES.as_posix(),
            "adsl_demo_csv": CSV_ADSL_DEMO.as_posix(),
            "merged_for_lm_csv": CSV_MERGED.as_posix(),
            "lab_feature_coverage_csv": CSV_LAB_FEATURE_COVERAGE.as_posix() if CSV_LAB_FEATURE_COVERAGE.exists() else None,
            "lab_features_dropped_csv": CSV_LAB_FEATURE_DROPPED.as_posix() if CSV_LAB_FEATURE_DROPPED.exists() else None,
        },
        "mart_10_key_check": {
            "union_keys": union_keys,
            "merged_keys": merged_keys,
        },
        "lab_feature_qc": {
            "min_non_missing_rate": MIN_LAB_NON_MISSING_RATE,
            "min_non_missing_count": MIN_LAB_NON_MISSING_COUNT,
            "dropped_all_missing_mean_cols": lab_dropped_all_missing,
            "filled_mean_with_median_n": int(len(lab_medians)),
        },
        "merged_missingness_tracking": {
            "numeric_na_cells_pre": int(numeric_na_pre),
            "numeric_na_cells_post": int(numeric_na_post),
            "numeric_na_cells_removed": int(numeric_na_removed),
            "bad_object_tokens_pre": int(bad_obj_tokens_pre),
            "bad_object_tokens_post": int(bad_obj_tokens_post),
            "bad_object_tokens_removed": int(bad_obj_tokens_removed),
            "aggressive_fill_numeric_na_to_zero": bool(FILL_REMAINING_NUMERIC_NA_WITH_ZERO),
        },
        "notes": [
            "MARTS nach V3 Prep_Pruefung_CDISC: Mart_01..Mart_10 in einem Skript.",
            "wide wird aus SDTM SV erzeugt (Pivot nach Besuchsreihenfolge) als Ersatz für Notebook-Vorbereitung.",
            "Target/Features folgen Notebook-Logik: Target = letzter Visit, Features = Historie (Leakage-Vermeidung).",
            "lab_values: Top-25 numeric LBTESTCD, Aggregation MEAN + count.",
            "Feature-QC im MARTS: Missing-Tokens normalisiert, Inf->NA, Coverage-Filter, all-missing LAB_MEAN drop, verbleibende LAB_MEAN NA->Median.",
            "Tracking: PRE/POST numeric-NA Zellen + bad string tokens im final merged.",
        ],
    }

    MANIFEST_OUT.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

    # ============================================================
    # 15) Console summary
    # ============================================================

    n_merged = con.execute("SELECT COUNT(*) FROM mart_merged_for_lm").fetchone()[0]
    n_cols = con.execute("SELECT COUNT(*) FROM pragma_table_info('mart_merged_for_lm')").fetchone()[0]
    dup_groups = con.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT USUBJID, COUNT(*) c
          FROM mart_merged_for_lm
          GROUP BY USUBJID
          HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    target_missing = con.execute(
        "SELECT COUNT(*) FROM mart_merged_for_lm WHERE TARGET_LOS_LAST IS NULL"
    ).fetchone()[0]

    print("\n" + "-" * 70)
    print("MARTS – SUMMARY")
    print("-" * 70)
    print(f"[out] merged_for_lm       : {CSV_MERGED}")
    print(f"[out] manifest            : {MANIFEST_OUT}")
    print(f"[qc ] Mart_10 union_keys  : {union_keys}")
    print(f"[qc ] Mart_10 merged_keys : {merged_keys}")
    print(f"[qc ] merged rows         : {n_merged}")
    print(f"[qc ] merged cols         : {n_cols}")
    print(f"[qc ] dup usubjid groups  : {dup_groups}")
    print(f"[qc ] target missing rows : {target_missing}")

    if has_lb:
        print(f"[qc ] lab coverage csv    : {CSV_LAB_FEATURE_COVERAGE}")
        if CSV_LAB_FEATURE_DROPPED.exists():
            print(f"[qc ] lab dropped csv     : {CSV_LAB_FEATURE_DROPPED}")
    print("=" * 70 + "\n")
    print("STEP 10 DONE")

finally:
    try:
        con.close()
    except Exception:
        pass
