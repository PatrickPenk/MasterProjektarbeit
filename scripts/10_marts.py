# ============================================================
# 10_marts_build.py
# MARTS (Mart_01 .. Mart_10) aus V3 Prep_Pruefung_CDISC.ipynb
# → zusammengefasst in EIN Skript, minimal angepasst für deine Architektur
#
# Laufposition: nach 09_adam_qc.py
#
# Orientierung (aus Notebook):
#   Mart_01: wide_all_visits (LOS_i in Minuten je Visit-Position)
#   Mart_02: wide_visits_cat (LOS_i + VISIT_i Kategorie nebeneinander)
#   Mart_03: wide_aggregation (Target = letzter Visit; Features = Historie)
#   Mart_04-07: lab_values (Top-25 LBTESTCD, numeric, Aggregation mean+count)
#   Mart_08: adsl_demo (AGE/SEX/RACE)
#   Mart_09: merged_for_LM (DuckDB SQL Merge)
#   Mart_10: Key-Union Check (union_keys vs merged_keys)
#
# Architektur-Anpassung (so wenig wie möglich):
#  - statt Notebook-Variablen (wide, sv, lb, adsl) laden wir aus DuckDB:
#      adam_adsl, sdtm_sv, sdtm_lb
#  - wir erzeugen "wide" aus sdtm_sv (Pivot nach Visit-Position) im Stil des Notebooks
#  - wir speichern Zwischenergebnisse wieder als DuckDB-Tabellen + CSV-Exports
#  - Manifest in outputs/manifest_marts.json
# ============================================================

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

import duckdb
import pandas as pd
import numpy as np

from scripts.config import DB_PATH, OUT_DIR, ensure_dirs

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 10 – MARTS (V3 Prep: Mart_01..Mart_10) → DuckDB/CSV/Manifest")
print("=" * 70)

# ============================================================
# 1) Manifest laden (Input aus Step 09 / Step 08)
# ============================================================

manifest_candidates = [
    OUT_DIR / "manifest_adam_checked.json",
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

MART_DIR = OUT_DIR / "marts"
MART_DIR.mkdir(parents=True, exist_ok=True)

CSV_WIDE = MART_DIR / "mart_wide_from_sv.csv"
CSV_WIDE_ALL_VISITS = MART_DIR / "mart_wide_all_visits.csv"
CSV_WIDE_VISITS_CAT = MART_DIR / "mart_wide_visits_cat.csv"
CSV_WIDE_AGG = MART_DIR / "mart_wide_aggregation.csv"
CSV_LAB_VALUES = MART_DIR / "mart_lab_values.csv"
CSV_ADSL_DEMO = MART_DIR / "mart_adsl_demo.csv"
CSV_MERGED = MART_DIR / "mart_merged_for_lm.csv"

MANIFEST_OUT = OUT_DIR / "manifest_marts.json"

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

def safe_write_csv(con: duckdb.DuckDBPyConnection, table: str, out_path: Path) -> None:
    if table_exists(con, table):
        con.execute(f"COPY {table} TO ? (HEADER, DELIMITER ',')", [str(out_path)])

def _safe_col(s: str) -> str:
    s = str(s)
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def _coerce_ts(series: pd.Series) -> pd.Series:
    # Notebook nutzt pd.to_datetime(errors="coerce")
    return pd.to_datetime(series, errors="coerce", utc=False)

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

    # Normalize keys
    for df in (adsl, sv, lb):
        if not df.empty and "USUBJID" in df.columns:
            df["USUBJID"] = df["USUBJID"].astype(str).replace({"": None, "nan": None, "None": None})

    # ============================================================
    # 5) (Architektur-Anpassung) wide aus SV erzeugen
    #    Notebook hat wide schon vorher aus Encounters-Pivot.
    #    Wir bauen wide minimal-äquivalent aus SDTM SV:
    #      - VISITID_i  := VISITNUM (wie Notebook wide VISITIDs)
    #      - START_i    := SVSTDTC
    #      - ENDE_i     := SVENDTC
    #
    #    Reihenfolge pro Patient:
    #      sort by SVSTDTC (fallback: VISITNUM)
    # ============================================================

    MAX_VISITS = 20

    # Spalten prüfen
    sv_cols = set(sv.columns)
    if "VISITNUM" not in sv_cols:
        raise RuntimeError("sdtm_sv hat keine VISITNUM – kann wide nicht deterministisch bauen.")
    if "VISIT" not in sv_cols:
        # Mart_02 braucht VISIT Kategorie; wenn fehlt, läuft es trotzdem, aber ohne Kategorien.
        sv["VISIT"] = pd.NA

    # timestamps
    if "SVSTDTC" in sv_cols:
        sv["SVSTDTC_TS"] = _coerce_ts(sv["SVSTDTC"])
    else:
        sv["SVSTDTC_TS"] = pd.NaT

    if "SVENDTC" in sv_cols:
        sv["SVENDTC_TS"] = _coerce_ts(sv["SVENDTC"])
    else:
        sv["SVENDTC_TS"] = pd.NaT

    sv["VISITNUM_NUM"] = pd.to_numeric(sv["VISITNUM"], errors="coerce")

    # Sortierung wie “Besuchsreihenfolge”
    sv_sorted = sv.sort_values(
        ["USUBJID", "SVSTDTC_TS", "VISITNUM_NUM"],
        ascending=[True, True, True],
        na_position="last",
    ).copy()

    # Sequenz pro Patient (1..)
    sv_sorted["SEQ"] = sv_sorted.groupby("USUBJID").cumcount() + 1
    sv_sorted = sv_sorted[sv_sorted["SEQ"] <= MAX_VISITS].copy()

    # Pivot wide: VISITID_i (=VISITNUM), START_i (=SVSTDTC), ENDE_i (=SVENDTC)
    wide_visitnum = sv_sorted.pivot(index="USUBJID", columns="SEQ", values="VISITNUM_NUM")
    wide_start = sv_sorted.pivot(index="USUBJID", columns="SEQ", values="SVSTDTC_TS")
    wide_end = sv_sorted.pivot(index="USUBJID", columns="SEQ", values="SVENDTC_TS")

    # zusammenbauen
    wide = pd.DataFrame({"USUBJID": wide_visitnum.index}).reset_index(drop=True)

    for i in range(1, MAX_VISITS + 1):
        if i in wide_visitnum.columns:
            wide[f"VISITID_{i}"] = wide_visitnum[i].values
        if i in wide_start.columns:
            wide[f"START_{i}"] = wide_start[i].values
        if i in wide_end.columns:
            wide[f"ENDE_{i}"] = wide_end[i].values

    # Save wide to DuckDB
    con.execute("DROP TABLE IF EXISTS mart_wide")
    con.register("wide_df", wide)
    con.execute("CREATE TABLE mart_wide AS SELECT * FROM wide_df")
    con.unregister("wide_df")

    # ============================================================
    # 6) Mart_01 – wide_all_visits (Notebook-Funktion fast 1:1)
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
            los = delta.dt.total_seconds() / 60  # Minuten

            if clamp_negative_to_zero:
                los = los.where(los.isna() | (los >= 0), 0)

            out[los_col] = los

            # WICHTIG: gleiche Spaltennamen behalten (VISITID_i), Inhalt wird LOS
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
    # 7) Mart_02 – wide_visits_cat (Notebook-Funktion fast 1:1)
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
    # 8) Mart_03 – wide_aggregation (Notebook-Funktion fast 1:1)
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
        long = long.dropna(subset=["LOS"])

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

        return wide_aggregation, long

    wide_aggregation, long_df = build_target_and_features_from_wide_visits_cat(wide_visits_cat, max_visits=MAX_VISITS)

    con.execute("DROP TABLE IF EXISTS mart_wide_aggregation")
    con.register("wide_aggregation_df", wide_aggregation)
    con.execute("CREATE TABLE mart_wide_aggregation AS SELECT * FROM wide_aggregation_df")
    con.unregister("wide_aggregation_df")

    # ============================================================
    # 9) Mart_04..07 – lab_values (Top-25 numeric, mean + count)
    #     (Notebook: Mart_04 load lb, Mart_05 optional n_unique,
    #      Mart_06 ranking, LM_07 pivot wide → lab_values)
    # ============================================================

    if has_lb and not lb.empty and "LBTESTCD" in lb.columns:
        lb2 = lb.copy()
        if "LBSTRESN" in lb2.columns:
            lb2["LBSTRESN"] = pd.to_numeric(lb2["LBSTRESN"], errors="coerce")
        else:
            lb2["LBSTRESN"] = np.nan

        # Ranking (Top-25 numeric)
        lb2["HAS_NUMERIC_VALUE"] = lb2["LBSTRESN"].notna()
        lb_numeric = lb2[lb2["HAS_NUMERIC_VALUE"]].copy()

        # Falls LBTEST fehlt, trotzdem gruppieren
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

        count_cols = [c for c in lab_values.columns if c.endswith("_count")]
        if count_cols:
            lab_values[count_cols] = lab_values[count_cols].fillna(0).astype("int64")

    else:
        # Kein LB → leere, joinbare Tabelle
        lab_values = pd.DataFrame({"USUBJID": wide_aggregation["USUBJID"].astype(str).unique()})
        # keine weiteren Features

    con.execute("DROP TABLE IF EXISTS mart_lab_values")
    con.register("lab_values_df", lab_values)
    con.execute("CREATE TABLE mart_lab_values AS SELECT * FROM lab_values_df")
    con.unregister("lab_values_df")

    # ============================================================
    # 10) Mart_08 – adsl_demo (Notebook 1:1)
    # ============================================================

    # Notebook: adsl_demo = adsl[["USUBJID","AGE","SEX","RACE"]]
    # In deiner Pipeline kann ARM etc existieren, wir bleiben minimal.
    keep_demo = [c for c in ["USUBJID", "AGE", "SEX", "RACE"] if c in adsl.columns]
    adsl_demo = adsl[keep_demo].copy()

    con.execute("DROP TABLE IF EXISTS mart_adsl_demo")
    con.register("adsl_demo_df", adsl_demo)
    con.execute("CREATE TABLE mart_adsl_demo AS SELECT * FROM adsl_demo_df")
    con.unregister("adsl_demo_df")

    # ============================================================
    # 11) Mart_09 – merged_for_LM (Notebook: DuckDB SQL)
    # ============================================================

    con.execute("DROP TABLE IF EXISTS mart_merged_for_lm")

    # Notebook-Query (minimal angepasst: Tabellen heißen mart_* bei uns)
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
    # 12) Mart_10 – Key-Union Check (Notebook 1:1 Logik)
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
    # 13) Exporte CSV (wie deine Architektur)
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
        },
        "mart_10_key_check": {
            "union_keys": union_keys,
            "merged_keys": merged_keys,
        },
        "notes": [
            "MARTS nach V3 Prep_Pruefung_CDISC: Mart_01..Mart_10 in einem Skript.",
            "wide wird aus SDTM SV erzeugt (Pivot nach Besuchsreihenfolge) als Ersatz für Notebook-Vorbereitung.",
            "Target/Features folgen Notebook-Logik: Target = letzter Visit, Features = Historie (Leakage-Vermeidung).",
            "lab_values: Top-25 numeric LBTESTCD, Aggregation MEAN + count, count-NaN->0.",
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
    print("=" * 70 + "\n")
    print("STEP 10 DONE")

finally:
    try:
        con.close()
    except Exception:
        pass
