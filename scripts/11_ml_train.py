# ============================================================
# 11_ml_train.py
# ML-Training (Notebook-nah: V3 Prep_Pruefung_CDISC.ipynb)
# Input : DuckDB table mart_merged_for_lm  (aus Step 10)
# Output: outputs/ml/*  + outputs/manifest_ml.json
#
# Notebook-Features (nahe 1:1):
#  - Dataset overview (rows/cols/cells)
#  - IQR-Spread-Heatmap (0–1 normalisiert)
#  - Capping / Winsorizing (Target + ausgewählte Feature-Heuristik)
#  - Stratified train/test split via pd.qcut(Target, q=10)
#  - Dataset A vs B (mit und ohne *_count Features)
#  - y-scaling (MinMax 0..1) + inverse_transform
#  - Modelvergleich per CV (GradientBoosting vs RandomForest)
#  - Holdout evaluation + mehrere Plots
# ============================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt  # matplotlib only

from sklearn.model_selection import train_test_split, KFold, cross_validate
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler, MinMaxScaler
from sklearn.impute import SimpleImputer

from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

import joblib

from scripts.config import DB_PATH, OUT_DIR, ensure_dirs
from html import escape
ensure_dirs()

print("\n" + "=" * 70)
print("STEP 11 – ML TRAIN (Notebook-nah) on mart_merged_for_lm")
print("=" * 70)

# ============================================================
# 1) Manifest laden (Input aus Step 10)
# ============================================================

manifest_candidates = [
    OUT_DIR / "manifest_marts.json",
    OUT_DIR / "manifest_adam_checked.json",
    OUT_DIR / "manifest_adam.json",
    OUT_DIR / "manifest_sdtm_checked.json",
    OUT_DIR / "manifest_sdtm.json",
]

manifest_in_path = next((p for p in manifest_candidates if p.exists()), None)
if manifest_in_path is None:
    raise FileNotFoundError(
        "Kein Manifest gefunden. Erwartet mindestens:\n"
        f" - {manifest_candidates[0]}\n"
        "Bitte zuerst Step 10 (MARTS) ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
run_id = (
    manifest_in.get("run_id")
    or manifest_in.get("marts", {}).get("run_id")
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

ML_DIR = OUT_DIR / "ml"
ML_DIR.mkdir(parents=True, exist_ok=True)

CSV_DATASET_OVERVIEW = ML_DIR / "dataset_overview.csv"
CSV_IQR_SPREAD = ML_DIR / "iqr_spread_numeric.csv"
CSV_CV_COMPARE = ML_DIR / "model_compare_cv.csv"
CSV_HOLDOUT_PREDS = ML_DIR / "holdout_predictions.csv"

MODEL_BEST_PKL = ML_DIR / "best_model.pkl"
JSON_SUMMARY = ML_DIR / "ml_summary.json"
MANIFEST_OUT = OUT_DIR / "manifest_ml.json"

# Plots (Notebook-like)
CHART_TARGET_HIST = ML_DIR / "chart_target_hist.png"
CHART_IQR_SPREAD = ML_DIR / "chart_iqr_spread_numeric.png"
CHART_CV_RMSE = ML_DIR / "chart_cv_rmse.png"
CHART_PRED_VS_TRUE = ML_DIR / "chart_pred_vs_true.png"
CHART_RESIDUALS = ML_DIR / "chart_residuals.png"
CHART_RESID_HIST = ML_DIR / "chart_residual_hist.png"

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

def safe_pct(n: int, d: int) -> float:
    return round((n / d) * 100.0, 2) if d else 0.0

def rmse(y_true, y_pred) -> float:
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))

def dataset_overview_df(df: pd.DataFrame, name: str) -> pd.DataFrame:
    rows = int(df.shape[0])
    cols = int(df.shape[1])
    cells = int(rows * cols)
    return pd.DataFrame([{
        "dataset": name,
        "rows": rows,
        "cols": cols,
        "cells": cells,
    }])

def cap_series(s: pd.Series, lower: Optional[float] = None, upper: Optional[float] = None) -> pd.Series:
    out = s.copy()
    if lower is not None:
        out = out.where(out.isna() | (out >= lower), lower)
    if upper is not None:
        out = out.where(out.isna() | (out <= upper), upper)
    return out

def infer_count_cols(cols: List[str]) -> List[str]:
    return [c for c in cols if c.lower().endswith("_count") or c.lower().endswith("count")]

# ============================================================
# 4) Load data (merged_for_LM)
# ============================================================

con = connect(DB_PATH)
try:
    if not table_exists(con, "mart_merged_for_lm"):
        raise RuntimeError("Tabelle 'mart_merged_for_lm' fehlt. Bitte Step 10 ausführen.")
    df = con.execute("SELECT * FROM mart_merged_for_lm").fetchdf()
finally:
    try:
        con.close()
    except Exception:
        pass

if df.empty:
    raise RuntimeError("mart_merged_for_lm ist leer – kein ML möglich.")

# ============================================================
# 4a) Dataset overview (Notebook-Output-like)
# ============================================================

overview = dataset_overview_df(df, "merged_for_LM")
overview.to_csv(CSV_DATASET_OVERVIEW, index=False)

# ============================================================
# 4b) (V3 Prep) EDA – Streuung pro numerischer Spalte (IQR, 0–1)
# ============================================================

num_cols_all = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

iqr_rows: List[Dict] = []
for c in num_cols_all:
    s = pd.to_numeric(df[c], errors="coerce")
    q1 = s.quantile(0.25)
    q3 = s.quantile(0.75)
    if pd.notna(q1) and pd.notna(q3):
        iqr_rows.append({"column": c, "iqr": float(q3 - q1)})

iqr_df = pd.DataFrame(iqr_rows).sort_values("iqr", ascending=False).reset_index(drop=True)

if not iqr_df.empty:
    vmin = float(iqr_df["iqr"].min())
    vmax = float(iqr_df["iqr"].max())
    if vmax > vmin:
        iqr_df["iqr_norm_0_1"] = (iqr_df["iqr"] - vmin) / (vmax - vmin)
    else:
        iqr_df["iqr_norm_0_1"] = 0.0

iqr_df.to_csv(CSV_IQR_SPREAD, index=False)

if not iqr_df.empty:
    plt.figure(figsize=(max(12, 0.25 * len(iqr_df)), 3))
    data = iqr_df["iqr_norm_0_1"].to_numpy().reshape(1, -1)
    im = plt.imshow(data, aspect="auto")
    plt.yticks([])
    plt.xticks(
        ticks=np.arange(len(iqr_df)),
        labels=iqr_df["column"].tolist(),
        rotation=90,
        ha="center",
    )
    plt.title("Streuung pro numerischer Spalte (IQR, 0–1 normalisiert)")
    cbar = plt.colorbar(im)
    cbar.set_label("IQR (Streuung)")
    plt.tight_layout()
    plt.savefig(CHART_IQR_SPREAD, dpi=150)
    plt.close()

# ============================================================
# 5) Target / Feature Setup (Notebook: Target = TARGET_LOS_LAST)
# ============================================================

ID_COL = "USUBJID"
TARGET_COL = "TARGET_LOS_LAST"

if ID_COL not in df.columns:
    raise RuntimeError("USUBJID fehlt in mart_merged_for_lm.")
if TARGET_COL not in df.columns:
    raise RuntimeError(f"Target-Spalte '{TARGET_COL}' fehlt in mart_merged_for_lm.")

n_total = len(df)
n_missing_target = int(df[TARGET_COL].isna().sum())

df_model = df[df[TARGET_COL].notna()].copy()
df_model[TARGET_COL] = pd.to_numeric(df_model[TARGET_COL], errors="coerce")
df_model = df_model[df_model[TARGET_COL].notna()].copy()

print(f"[ml ] rows total          : {n_total}")
print(f"[ml ] target missing      : {n_missing_target} ({safe_pct(n_missing_target, n_total)}%)")
print(f"[ml ] rows used           : {len(df_model)}")

# Plot target histogram (Notebook-like)
plt.figure()
plt.hist(df_model[TARGET_COL].values, bins=30)
plt.xlabel(TARGET_COL)
plt.ylabel("count")
plt.title("Target distribution")
plt.tight_layout()
plt.savefig(CHART_TARGET_HIST, dpi=150)
plt.close()

# ============================================================
# 6) Capping (Notebook-typisch)
#    - Target cap (upper) aus Quantil-Heuristik
#    - Feature caps: nur für LOS_* numerische Spalten (Heuristik)
# ============================================================

# Target cap: z.B. 99.5% Quantil
target_cap_q = 0.995
target_upper = float(df_model[TARGET_COL].quantile(target_cap_q))
df_model[TARGET_COL] = cap_series(df_model[TARGET_COL], lower=0.0, upper=target_upper)

# Feature caps (Heuristik: LOS_* und *MEAN*/*MAX*): cap bei 99.5%
feature_cap_q = 0.995
feature_caps: Dict[str, float] = {}

for c in df_model.columns:
    if c == TARGET_COL or c == ID_COL:
        continue
    if pd.api.types.is_numeric_dtype(df_model[c]):
        # Notebook-ähnlicher Fokus: LOS_* & zusammengefasste LOS-Features
        if c.upper().startswith("LOS_") or "LOS_" in c.upper() or "LOS" in c.upper():
            capv = df_model[c].quantile(feature_cap_q)
            if pd.notna(capv):
                feature_caps[c] = float(capv)
                df_model[c] = cap_series(df_model[c], lower=None, upper=float(capv))

# ============================================================
# 7) Build Dataset A/B (Notebook: mit vs ohne count_cols)
# ============================================================

DROP_ALWAYS = [ID_COL]  # ID raus aus Features
if "TARGET_LAST_VISIT_CAT" in df_model.columns:
    # Notebook-typisch: letzter Visit als Target-nah -> nicht als Feature
    DROP_ALWAYS.append("TARGET_LAST_VISIT_CAT")

X_full = df_model.drop(columns=[c for c in DROP_ALWAYS + [TARGET_COL] if c in df_model.columns], errors="ignore")
y_raw = df_model[TARGET_COL].astype(float).values
keys = df_model[ID_COL].astype(str).values

count_cols = infer_count_cols(list(X_full.columns))

# Dataset A: alle Features (inkl. counts)
X_A = X_full.copy()

# Dataset B: drop count_cols
X_B = X_full.drop(columns=count_cols, errors="ignore")

print(f"[ml ] features A total    : {X_A.shape[1]}")
print(f"[ml ] count cols detected : {len(count_cols)}")
print(f"[ml ] features B total    : {X_B.shape[1]}")

# ============================================================
# 8) y scaling (Notebook: MinMax 0..1) + inverse helper
# ============================================================

y_scaler = MinMaxScaler()
y_scaled = y_scaler.fit_transform(y_raw.reshape(-1, 1)).ravel()

def inverse_y(y_scaled_1d: np.ndarray) -> np.ndarray:
    return y_scaler.inverse_transform(y_scaled_1d.reshape(-1, 1)).ravel()

# ============================================================
# 9) Stratified train/test split via qcut(Target, q=10)
# ============================================================

# qcut kann bei zu wenig unique values fail -> fallback ohne stratify
bins = None
try:
    bins = pd.qcut(y_raw, q=10, duplicates="drop")
except Exception:
    bins = None

def split_dataset(X: pd.DataFrame, y_s: np.ndarray, y_r: np.ndarray, keys: np.ndarray):
    if bins is not None:
        return train_test_split(X, y_s, y_r, keys, test_size=0.2, random_state=42, stratify=bins)
    return train_test_split(X, y_s, y_r, keys, test_size=0.2, random_state=42)

XA_tr, XA_te, yA_tr, yA_te, yAraw_tr, yAraw_te, kA_tr, kA_te = split_dataset(X_A, y_scaled, y_raw, keys)
XB_tr, XB_te, yB_tr, yB_te, yBraw_tr, yBraw_te, kB_tr, kB_te = split_dataset(X_B, y_scaled, y_raw, keys)

# ============================================================
# 10) Preprocessing (Notebook-typisch)
#     - numeric: median impute + scaler
#     - categorical: most_frequent + onehot
# ============================================================

def make_preprocess(X: pd.DataFrame) -> ColumnTransformer:
    num_cols = [c for c in X.columns if pd.api.types.is_numeric_dtype(X[c])]
    cat_cols = [c for c in X.columns if c not in num_cols]

    numeric_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=True)),
        ]
    )
    return ColumnTransformer(
        transformers=[
            ("num", numeric_transformer, num_cols),
            ("cat", categorical_transformer, cat_cols),
        ],
        remainder="drop",
    )

# ============================================================
# 11) Modelvergleich CV: GB vs RF (für Dataset A und B)
# ============================================================

cv = KFold(n_splits=5, shuffle=True, random_state=42)
scoring = {
    "mae": "neg_mean_absolute_error",
    "rmse": "neg_root_mean_squared_error",
    "r2": "r2",
}

# Notebook-nahe Default-Modelle
gb = GradientBoostingRegressor(
    random_state=42,
    n_estimators=400,
    learning_rate=0.05,
    max_depth=3,
    subsample=0.9,
)
rf = RandomForestRegressor(
    random_state=42,
    n_estimators=500,
    n_jobs=-1,
    min_samples_leaf=2,
)

def cv_compare(name_prefix: str, Xtr: pd.DataFrame, ytr: np.ndarray) -> List[Dict]:
    pre = make_preprocess(Xtr)
    rows: List[Dict] = []
    for model_name, est in [("GradientBoosting", gb), ("RandomForest", rf)]:
        pipe = Pipeline(steps=[("preprocess", pre), ("model", est)])
        res = cross_validate(pipe, Xtr, ytr, cv=cv, scoring=scoring, n_jobs=-1, return_train_score=False)

        rows.append({
            "dataset": name_prefix,
            "model": model_name,
            "cv_mae": float(-np.mean(res["test_mae"])),
            "cv_rmse": float(-np.mean(res["test_rmse"])),
            "cv_r2": float(np.mean(res["test_r2"])),
            "n_train": int(len(Xtr)),
            "n_features_raw": int(Xtr.shape[1]),
        })
    return rows

rows_cv: List[Dict] = []
rows_cv += cv_compare("A_with_counts", XA_tr, yA_tr)
rows_cv += cv_compare("B_without_counts", XB_tr, yB_tr)

cv_df = pd.DataFrame(rows_cv).sort_values(["dataset", "cv_rmse", "cv_mae"], ascending=[True, True, True]).reset_index(drop=True)
cv_df.to_csv(CSV_CV_COMPARE, index=False)

# CV RMSE chart (Notebook-like)
plt.figure()
labels = (cv_df["dataset"] + ":" + cv_df["model"]).tolist()
plt.bar(labels, cv_df["cv_rmse"].values)
plt.xticks(rotation=45, ha="right")
plt.ylabel("CV RMSE (y scaled 0–1)")
plt.title("Cross-validation RMSE (scaled target)")
plt.tight_layout()
plt.savefig(CHART_CV_RMSE, dpi=150)
plt.close()

# Pick best by CV RMSE across both datasets
best_row = cv_df.sort_values(["cv_rmse", "cv_mae"], ascending=[True, True]).iloc[0].to_dict()
best_dataset = best_row["dataset"]
best_model_name = best_row["model"]

print(f"[ml ] best dataset/model  : {best_dataset} / {best_model_name}")

# Build best pipeline and evaluate on corresponding holdout
def get_split_for_best():
    if best_dataset == "A_with_counts":
        return (XA_tr, XA_te, yA_tr, yA_te, yAraw_te, kA_te)
    return (XB_tr, XB_te, yB_tr, yB_te, yBraw_te, kB_te)

Xtr, Xte, ytr, yte, yraw_te, kte = get_split_for_best()

pre_best = make_preprocess(Xtr)
est_best = gb if best_model_name == "GradientBoosting" else rf
best_pipe = Pipeline(steps=[("preprocess", pre_best), ("model", est_best)])
best_pipe.fit(Xtr, ytr)

y_pred_scaled = best_pipe.predict(Xte)
y_pred_raw = inverse_y(np.asarray(y_pred_scaled))

# Metrics on RAW scale (Notebook: inverse transform)
holdout_mae = float(mean_absolute_error(yraw_te, y_pred_raw))
holdout_rmse = float(rmse(yraw_te, y_pred_raw))
holdout_r2 = float(r2_score(yraw_te, y_pred_raw))

pred_df = pd.DataFrame({
    "USUBJID": kte,
    "y_true": yraw_te,
    "y_pred": y_pred_raw,
    "residual": (yraw_te - y_pred_raw),
    "y_true_scaled": yte,
    "y_pred_scaled": y_pred_scaled,
})
pred_df.to_csv(CSV_HOLDOUT_PREDS, index=False)

# ============================================================
# 12) Plots (Notebook-like diagnostics)
# ============================================================

# Pred vs True
plt.figure()
plt.scatter(pred_df["y_true"], pred_df["y_pred"])
plt.xlabel("True LOS (raw)")
plt.ylabel("Predicted LOS (raw)")
plt.title("Holdout: Predicted vs True")
plt.tight_layout()
plt.savefig(CHART_PRED_VS_TRUE, dpi=150)
plt.close()

# Residuals vs Pred
plt.figure()
plt.scatter(pred_df["y_pred"], pred_df["residual"])
plt.axhline(0.0)
plt.xlabel("Predicted LOS (raw)")
plt.ylabel("Residual (true - pred)")
plt.title("Holdout: Residuals vs Predicted")
plt.tight_layout()
plt.savefig(CHART_RESIDUALS, dpi=150)
plt.close()

# Residual histogram
plt.figure()
plt.hist(pred_df["residual"].values, bins=30)
plt.xlabel("Residual (true - pred)")
plt.ylabel("count")
plt.title("Holdout: Residual distribution")
plt.tight_layout()
plt.savefig(CHART_RESID_HIST, dpi=150)
plt.close()




REPORT_HTML = ML_DIR / "ml_report.html"

def html_table(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return "<p><em>(keine Daten)</em></p>"
    d = df.copy()
    if len(d) > max_rows:
        d = d.head(max_rows)
        note = f"<p><em>Hinweis: nur Top {max_rows} Zeilen angezeigt.</em></p>"
    else:
        note = ""
    return note + d.to_html(index=False, escape=True)

css = """
<style>
  body { font-family: Arial, sans-serif; margin: 24px; }
  h1,h2,h3 { margin-bottom: 8px; }
  .meta { color: #333; margin-bottom: 16px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .card { border: 1px solid #ddd; border-radius: 10px; padding: 14px; background: #fff; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0 22px 0; }
  th, td { border: 1px solid #ddd; padding: 8px; font-size: 13px; }
  th { background: #f3f3f3; }
  code { background: #f7f7f7; padding: 2px 4px; border-radius: 3px; }
  img { max-width: 100%; border: 1px solid #ddd; border-radius: 8px; }
</style>
"""

# kleine Holdout-Zusammenfassung als DF
holdout_summary_df = pd.DataFrame([{
    "best_dataset": best_dataset,
    "best_model": best_model_name,
    "holdout_mae_raw": holdout_mae,
    "holdout_rmse_raw": holdout_rmse,
    "holdout_r2_raw": holdout_r2,
    "n_test": int(len(pred_df)),
}])

# iqr_df kann riesig sein -> im Report nur Top 80 anzeigen
IQR_TOP_N = 80

html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>ML Report – {escape(run_id)}</title>
  {css}
</head>
<body>
  <h1>ML Report (Step 11)</h1>

  <div class="meta">
    <div><b>run_id</b>: <code>{escape(str(run_id))}</code></div>
    <div><b>run_ts_utc</b>: {escape(RUN_TS.isoformat())}</div>
    <div><b>db</b>: <code>{escape(DB_PATH.as_posix())}</code></div>
    <div><b>input</b>: <code>mart_merged_for_lm</code></div>
    <div><b>target</b>: <code>{escape(TARGET_COL)}</code></div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Dataset Overview</h2>
      {html_table(overview)}
    </div>
    <div class="card">
      <h2>Holdout Summary</h2>
      {html_table(holdout_summary_df)}
    </div>
  </div>

  <h2>Cross Validation Vergleich</h2>
  {html_table(cv_df, max_rows=20)}

  <h2>IQR Streuung (Top {IQR_TOP_N})</h2>
  {html_table(iqr_df.head(IQR_TOP_N), max_rows=IQR_TOP_N)}

  <h2>Plots</h2>
  <div class="grid">
    <div class="card">
      <h3>Target Histogram</h3>
      <img src="{escape(CHART_TARGET_HIST.name)}">
    </div>
    <div class="card">
      <h3>IQR Spread</h3>
      <img src="{escape(CHART_IQR_SPREAD.name)}">
    </div>
    <div class="card">
      <h3>CV RMSE</h3>
      <img src="{escape(CHART_CV_RMSE.name)}">
    </div>
    <div class="card">
      <h3>Pred vs True</h3>
      <img src="{escape(CHART_PRED_VS_TRUE.name)}">
    </div>
    <div class="card">
      <h3>Residuals</h3>
      <img src="{escape(CHART_RESIDUALS.name)}">
    </div>
    <div class="card">
      <h3>Residual Histogram</h3>
      <img src="{escape(CHART_RESID_HIST.name)}">
    </div>
  </div>

  <h2>Artefakte</h2>
  <ul>
    <li>CV Compare CSV: <code>{escape(CSV_CV_COMPARE.name)}</code></li>
    <li>Holdout Predictions CSV: <code>{escape(CSV_HOLDOUT_PREDS.name)}</code></li>
    <li>IQR Spread CSV: <code>{escape(CSV_IQR_SPREAD.name)}</code></li>
    <li>Dataset Overview CSV: <code>{escape(CSV_DATASET_OVERVIEW.name)}</code></li>
    <li>Model: <code>{escape(MODEL_BEST_PKL.name)}</code></li>
    <li>Summary JSON: <code>{escape(JSON_SUMMARY.name)}</code></li>
  </ul>

</body>
</html>
"""

REPORT_HTML.write_text(html_doc, encoding="utf-8")
print(f"[out] html report          : {REPORT_HTML}")


# ============================================================
# 13) Persist model + summaries
# ============================================================

joblib.dump(best_pipe, MODEL_BEST_PKL)

summary = {
    "run_id": run_id,
    "run_ts_utc": RUN_TS.isoformat(),
    "db_path": DB_PATH.as_posix(),
    "input_table": "mart_merged_for_lm",
    "id_col": ID_COL,
    "target_col": TARGET_COL,
    "target_cap_q": target_cap_q,
    "target_upper_cap": target_upper,
    "feature_cap_q": feature_cap_q,
    "feature_caps_applied_n": int(len(feature_caps)),
    "rows_total": int(n_total),
    "rows_used": int(len(df_model)),
    "target_missing_rows": int(n_missing_target),
    "dataset_A_features": int(X_A.shape[1]),
    "dataset_B_features": int(X_B.shape[1]),
    "count_cols_detected": int(len(count_cols)),
    "best_choice": {
        "dataset": best_dataset,
        "model": best_model_name,
        "cv_rmse_scaled": float(best_row["cv_rmse"]),
        "cv_mae_scaled": float(best_row["cv_mae"]),
        "cv_r2_scaled": float(best_row["cv_r2"]),
    },
    "holdout_metrics_raw": {
        "mae": holdout_mae,
        "rmse": holdout_rmse,
        "r2": holdout_r2,
        "n_test": int(len(pred_df)),
    },
    "outputs": {
        "dataset_overview_csv": CSV_DATASET_OVERVIEW.as_posix(),
        "iqr_spread_csv": CSV_IQR_SPREAD.as_posix(),
        "cv_compare_csv": CSV_CV_COMPARE.as_posix(),
        "holdout_predictions_csv": CSV_HOLDOUT_PREDS.as_posix(),
        "best_model_pkl": MODEL_BEST_PKL.as_posix(),
        "charts": {
            "target_hist": CHART_TARGET_HIST.as_posix(),
            "iqr_spread": CHART_IQR_SPREAD.as_posix(),
            "cv_rmse": CHART_CV_RMSE.as_posix(),
            "pred_vs_true": CHART_PRED_VS_TRUE.as_posix(),
            "residuals": CHART_RESIDUALS.as_posix(),
            "residual_hist": CHART_RESID_HIST.as_posix(),
        },
    },
}

JSON_SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

manifest_out = dict(manifest_in)
manifest_out["ml"] = {
    "run_id": run_id,
    "trained_at_utc": RUN_TS.isoformat(),
    "input_table": "mart_merged_for_lm",
    "target": TARGET_COL,
    "best_dataset": best_dataset,
    "best_model": best_model_name,
    "holdout_metrics_raw": summary["holdout_metrics_raw"],
    "outputs": summary["outputs"],
    "source_manifest": manifest_in_path.name,
    "html_report": REPORT_HTML.as_posix(),
    "notes": [
        "Notebook-nah: A/B (counts vs no-counts), qcut-stratify split, y MinMax scaling + inverse.",
        "Modelvergleich: GradientBoosting vs RandomForest per CV auf scaled target.",
        "Plots + CSV Artefakte wie Notebook-Outputs.",
    ],
}
MANIFEST_OUT.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

# ============================================================
# 14) Console summary
# ============================================================

print("\n" + "-" * 70)
print("ML TRAIN – SUMMARY")
print("-" * 70)
print(f"[out] dataset overview    : {CSV_DATASET_OVERVIEW}")
print(f"[out] iqr spread          : {CSV_IQR_SPREAD}")
print(f"[out] cv compare          : {CSV_CV_COMPARE}")
print(f"[out] holdout preds       : {CSV_HOLDOUT_PREDS}")
print(f"[out] best model          : {MODEL_BEST_PKL}")
print(f"[out] charts              :")  # noqa: F541
print(f"      - {CHART_TARGET_HIST.name}")
print(f"      - {CHART_IQR_SPREAD.name}")
print(f"      - {CHART_CV_RMSE.name}")
print(f"      - {CHART_PRED_VS_TRUE.name}")
print(f"      - {CHART_RESIDUALS.name}")
print(f"      - {CHART_RESID_HIST.name}")
print(f"[out] summary json         : {JSON_SUMMARY}")
print(f"[out] manifest            : {MANIFEST_OUT}")
print(f"[ml ] best               : {best_dataset} / {best_model_name}")
print(f"[ml ] holdout MAE (raw)  : {holdout_mae:.4f}")
print(f"[ml ] holdout RMSE (raw) : {holdout_rmse:.4f}")
print(f"[ml ] holdout R2 (raw)   : {holdout_r2:.4f}")
print("=" * 70 + "\n")
print("STEP 11 DONE")

