# ============================================================
# 02_raw_profiling.py
# ============================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple, Dict, List

import numpy as np
import pandas as pd

from scripts.config import RAW_PROF_DIR, MANIFEST_DIR, ensure_dirs, CORE_TABLES
ensure_dirs()

# ============================================================
print("\n" + "=" * 60)
print("STEP 02 - RAW PROFILING")
print("=" * 60)

# ============================================================
# 1. Manifest laden (Input aus Step 01)
# ============================================================

manifest_in_path = MANIFEST_DIR / "manifest_raw.json"
if not manifest_in_path.exists():
    raise FileNotFoundError(
        f"Manifest nicht gefunden: {manifest_in_path}\n"
        "Bitte zuerst Step 02 ausführen: python -m scripts.01_download_extract"
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))

csv_root = Path(manifest_in["csv_root"])
paths: Dict[str, Path] = {k: Path(v) for k, v in manifest_in["csv_paths"].items()}

print(f"[in] manifest           : {manifest_in_path}")
print(f"[in] csv_root           : {csv_root}\n")

# ============================================================
# 2. Core Tables Contract
# ============================================================

core_present = [t for t in CORE_TABLES if t in paths]
core_missing = [t for t in CORE_TABLES if t not in paths]

print(f"[plan] core_tables        : {', '.join(CORE_TABLES)}")
print(f"[plan] core_present       : {', '.join(core_present) if core_present else '(none)'}")
if core_missing:
    print(f"[warn] core_missing       : {', '.join(core_missing)}")

STRICT_CORE_TABLES = False  
if STRICT_CORE_TABLES and core_missing:
    raise RuntimeError(f"Missing CORE tables: {core_missing}")

PROFILE_ONLY_CORE = True 
if PROFILE_ONLY_CORE:
    tables_to_profile = core_present
else:
    tables_to_profile = sorted(paths.keys())

print(f"[plan] tables_to_profile  : {', '.join(tables_to_profile) if tables_to_profile else '(none)'}\n")

# ============================================================
# 2. Funktionen Definieren
# ============================================================

# Spalten mit Datum suchen
def detect_date_cols(cols: List[str]) -> List[str]:
    pats = ("date", "dt", "start", "stop", "birth", "death")
    return [c for c in cols if any(p in c.lower() for p in pats)]

# QC summary der Spalten
def profile_df(df: pd.DataFrame, name: str) -> Tuple[dict, Dict[str, float]]:
    n_rows, n_cols = df.shape
    dup_rows = int(df.duplicated().sum())

    miss_rate = df.isna().mean().sort_values(ascending=False)
    top_missing = miss_rate.head(10)

    date_cols = detect_date_cols(list(df.columns))
    date_parse_stats: Dict[str, float] = {}
    
    for c in date_cols:
        s = df[c]
        parsed = pd.to_datetime(s, errors="coerce", utc=False)
        # nur non-null Werte bewerten (damit Missing != Parse-Fail)
        date_parse_stats[c] = float(parsed[s.notna()].isna().mean()) if s.notna().any() else 0.0

    prof = {
        "table": name,
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "duplicate_rows": dup_rows,
        "n_date_cols_detected": int(len(date_cols)),
        "worst_date_parse_invalid_rate": (max(date_parse_stats.values()) if date_parse_stats else np.nan),
        "top_missing_cols": "; ".join([f"{idx}:{val:.2%}" for idx, val in top_missing.items()]),
    }
    return prof, date_parse_stats

# ============================================================
# 3. Ausführen
# ============================================================

profiles = []
date_quality_details = []

# deterministische Reihenfolge
for table in tables_to_profile:
    csv_path = paths[table]
    df = pd.read_csv(csv_path, low_memory=False)

    prof, date_stats = profile_df(df, table)
    profiles.append(prof)

    for col, invalid_rate in date_stats.items():
        date_quality_details.append({
            "table": table,
            "column": col,
            "invalid_parse_rate": invalid_rate
        })

profiles_df = pd.DataFrame(profiles).sort_values("table")
date_detail_df = pd.DataFrame(date_quality_details).sort_values("invalid_parse_rate", ascending=False)

# Outputs
profiling_summary_path = RAW_PROF_DIR / "raw_profiling_summary.csv"
date_quality_path      = RAW_PROF_DIR / "raw_datefield_parse_quality.csv"

profiles_df.to_csv(profiling_summary_path, index=False)
date_detail_df.to_csv(date_quality_path, index=False)


# ============================================================
# 4. Ausgabe
# ============================================================

print("\n" + "-" * 60)
print("RAW PROFILING – SUMMARY")
print("-" * 60)
print(f"[out] profiling summary   : {profiling_summary_path}")
print(f"[out] date quality        : {date_quality_path}")
print(f"[out] tables profiled     : {len(profiles_df)}")
if not date_detail_df.empty:
    worst = date_detail_df.iloc[0]
    print(f"[dq ] worst_parse_rate    : {worst['invalid_parse_rate']:.2%} ({worst['table']}.{worst['column']})")
else:
    print("[dq ] worst_parse_rate    : n/a (keine Date-Spalten erkannt)")


# ============================================================
# 5. Manifest updaten
# ============================================================
manifest_out = dict(manifest_in)
manifest_out.update({
    "core_tables": CORE_TABLES,
    "core_present": core_present,
    "core_missing": core_missing,
    "profiling": {
        "raw_profiling_summary_csv": profiling_summary_path.as_posix(),
        "raw_datefield_parse_quality_csv": date_quality_path.as_posix(),
        "profile_only_core": PROFILE_ONLY_CORE,
    }
})

manifest_out_path = MANIFEST_DIR / "manifest_raw_profiled.json"
manifest_out_path.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

print(f"[out] manifest            : {manifest_out_path}")
print("=" * 60 + "\n")
