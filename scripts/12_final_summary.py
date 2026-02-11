# ============================================================
# 12_final_report.py
# FINAL REPORT (JSON + HTML) in OUT_DIR/final/
# Reads manifests from MANIFEST_DIR (out/manifests/)
# ============================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Dict, Any, List, Optional

from scripts.config import MANIFEST_DIR, OUT_DIR, DB_PATH, ensure_dirs

ensure_dirs()

print("\n" + "=" * 70)
print("STEP 12 – FINAL REPORT")
print("=" * 70)

RUN_TS = datetime.now(timezone.utc)

# ✅ Final outputs gehören nach OUT_DIR/final
FINAL_DIR = OUT_DIR / "final"
FINAL_DIR.mkdir(parents=True, exist_ok=True)

FINAL_JSON = FINAL_DIR / "final_summary.json"
FINAL_HTML = FINAL_DIR / "final_report.html"

# ------------------------------------------------------------
# 1) Manifeste einsammeln (aus MANIFEST_DIR)
# ------------------------------------------------------------

MANIFEST_PATHS = {
    "Rohdaten": MANIFEST_DIR / "manifest_raw.json",
    "Staging-Qualitätsprüfung": MANIFEST_DIR / "manifest_staging_checked.json",
    "SDTM-Erstellung & Prüfung": MANIFEST_DIR / "manifest_sdtm_checked.json",
    "ADaM-Erstellung & Prüfung": MANIFEST_DIR / "manifest_adam_checked.json",
    "Analyse-Datensatz (Marts)": MANIFEST_DIR / "manifest_marts.json",
    "Modelltraining (ML)": MANIFEST_DIR / "manifest_ml.json",
}


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


manifests: Dict[str, Dict[str, Any]] = {}
missing: List[str] = []

for phase, path in MANIFEST_PATHS.items():
    if path.exists():
        m = load_json(path)
        if isinstance(m, dict):
            manifests[phase] = m
        else:
            missing.append(phase)
    else:
        missing.append(phase)


def deep_find_run_id(obj: Any) -> Optional[str]:
    """Findet run_id robust auch in verschachtelten Strukturen."""
    if isinstance(obj, dict):
        for k in ("run_id", "Run-ID", "RUN_ID", "runId", "runID"):
            v = obj.get(k)
            if v:
                return str(v)
        for v in obj.values():
            rid = deep_find_run_id(v)
            if rid:
                return rid
    elif isinstance(obj, list):
        for it in obj:
            rid = deep_find_run_id(it)
            if rid:
                return rid
    return None


def pick_run_id() -> str:
    # möglichst konsistent: ML > Marts > ADaM > SDTM > Rohdaten
    for phase in [
        "Modelltraining (ML)",
        "Analyse-Datensatz (Marts)",
        "ADaM-Erstellung & Prüfung",
        "SDTM-Erstellung & Prüfung",
        "Rohdaten",
    ]:
        m = manifests.get(phase, {})
        rid = deep_find_run_id(m)
        if rid:
            return rid
    # fallback: notfalls aus irgendeinem manifest_*.json im MANIFEST_DIR ziehen
    for p in sorted(MANIFEST_DIR.glob("manifest_*.json")):
        d = load_json(p)
        rid = deep_find_run_id(d)
        if rid:
            return rid
    return "UNBEKANNT"


RUN_ID = pick_run_id()

# ------------------------------------------------------------
# 2) Status der Pipeline (lesbar, ohne Code-Sprache)
# ------------------------------------------------------------

PHASE_ORDER = [
    "Rohdaten",
    "Staging-Qualitätsprüfung",
    "SDTM-Erstellung & Prüfung",
    "ADaM-Erstellung & Prüfung",
    "Analyse-Datensatz (Marts)",
    "Modelltraining (ML)",
]

pipeline_status: List[Dict[str, Any]] = []
for phase in PHASE_ORDER:
    ok = phase in manifests
    pipeline_status.append(
        {"Phase": phase, "Status": "Erfolgreich" if ok else "Fehlt / nicht ausgeführt"}
    )

overall_success = all(p["Status"] == "Erfolgreich" for p in pipeline_status)

# ------------------------------------------------------------
# 3) Hilfsfunktionen: Tabellen, Bildpfade, Zahlenformat
# ------------------------------------------------------------

def rel_to_final(path_str: str) -> str:
    """
    HTML wird nach OUT_DIR/final/ geschrieben.
    Wir referenzieren Artefakte möglichst relativ von final/ aus:
      - absolute Pfade -> relativ zu OUT_DIR -> ../<rel>
      - relative Pfade, die unter OUT_DIR existieren -> ../<rel>
    """
    try:
        p = Path(path_str)

        if p.is_absolute():
            try:
                p = p.relative_to(OUT_DIR)
                return escape(str(Path("..") / p))
            except Exception:
                return escape(p.name)

        if (OUT_DIR / p).exists():
            return escape(str(Path("..") / p))

        return escape(p.as_posix())
    except Exception:
        return escape(path_str)


def html_table(rows: List[Dict[str, Any]], empty_msg: str = "(keine Daten)") -> str:
    if not rows:
        return f"<p><em>{escape(empty_msg)}</em></p>"

    cols = list(rows[0].keys())
    thead = "<tr>" + "".join(f"<th>{escape(str(c))}</th>" for c in cols) + "</tr>"
    tbody = ""
    for r in rows:
        tbody += "<tr>" + "".join(f"<td>{escape(str(r.get(c, '')))}</td>" for c in cols) + "</tr>"
    return f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"


def nice_metric(value: Any, digits: int = 4) -> str:
    try:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "Ja" if value else "Nein"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if abs(value) >= 1000:
                return f"{value:,.0f}"
            return f"{value:.{digits}f}"
        return str(value)
    except Exception:
        return str(value)


def file_exists(path_str: Optional[str]) -> bool:
    if not path_str:
        return False
    try:
        p = Path(path_str)
        if p.is_absolute():
            return p.exists()
        if (OUT_DIR / p).exists():
            return True
        return p.exists()
    except Exception:
        return False


def render_charts(charts: List[Dict[str, str]]) -> str:
    if not charts:
        return "<p><em>(keine Grafiken gefunden)</em></p>"
    blocks = []
    for c in charts:
        blocks.append(
            f"""
            <div class="card">
              <h3>{escape(c["title"])}</h3>
              <img src="{rel_to_final(c["path"])}" alt="{escape(c["title"])}">
              <div class="small">Datei: <code>{escape(Path(c["path"]).name)}</code></div>
            </div>
            """
        )
    return "<div class='grid'>" + "\n".join(blocks) + "</div>"

# ------------------------------------------------------------
# 4) Inhalte pro Phase extrahieren
# ------------------------------------------------------------

# --- ADaM QC (Step 09) ---
adam_m = manifests.get("ADaM-Erstellung & Prüfung", {})
adam_qc = (adam_m.get("adam_qc") or {})
adam_hard_fail_count = adam_qc.get("hard_fail_count", None)
adam_strict = adam_qc.get("strict_qc", None)
adam_outputs = (adam_qc.get("outputs") or {})

adam_qc_charts: List[Dict[str, str]] = []
charts_adam = (adam_outputs.get("charts") or {})
for label, k in [
    ("Vollständigkeit (Coverage)", "coverage"),
    ("Plausibilität", "plausibility"),
    ("Schlüssel-/Beziehungsprüfung", "ri"),
]:
    p = charts_adam.get(k)
    if p and file_exists(p):
        adam_qc_charts.append({"title": label, "path": p})

# --- Marts (Step 10) ---
marts_m = manifests.get("Analyse-Datensatz (Marts)", {})
marts_block = marts_m.get("marts") or {}

marts_tables = marts_block.get("tables") or {}
marts_outputs = marts_block.get("outputs") or {}
marts_keycheck = marts_block.get("mart_10_key_check") or {}

# Zusätzliche Marts-Kennzahlen direkt aus DuckDB (robust)
marts_metrics: Dict[str, Any] = {}
try:
    import duckdb
    con2 = duckdb.connect(str(DB_PATH))

    exists = con2.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name='mart_merged_for_lm' LIMIT 1"
    ).fetchone() is not None

    if exists:
        n_rows = con2.execute("SELECT COUNT(*) FROM mart_merged_for_lm").fetchone()[0]
        n_cols = con2.execute("SELECT COUNT(*) FROM pragma_table_info('mart_merged_for_lm')").fetchone()[0]
        dup_groups = con2.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT USUBJID, COUNT(*) c
              FROM mart_merged_for_lm
              GROUP BY USUBJID
              HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        target_missing = con2.execute(
            "SELECT COUNT(*) FROM mart_merged_for_lm WHERE TARGET_LOS_LAST IS NULL"
        ).fetchone()[0]

        marts_metrics = {
            "Anzahl Zeilen (Analyse-Datensatz)": int(n_rows),
            "Anzahl Spalten (Analyse-Datensatz)": int(n_cols),
            "Doppelte Patienten (USUBJID-Gruppen)": int(dup_groups),
            "Fehlende Zielwerte (Aufenthaltsdauer)": int(target_missing),
        }
    else:
        marts_metrics = {"Hinweis": "Tabelle mart_merged_for_lm nicht gefunden (Step 10 ausgeführt?)"}
finally:
    try:
        con2.close()
    except Exception:
        pass

# --- ML (Step 11) ---
ml_m = manifests.get("Modelltraining (ML)", {})
ml_block = ml_m.get("ml") or {}
ml_best_model = ml_block.get("best_model") or ml_block.get("best_model_name")
ml_best_dataset = ml_block.get("best_dataset")
ml_holdout = ml_block.get("holdout_metrics_raw") or ml_block.get("holdout_metrics") or {}
ml_outputs = ml_block.get("outputs") or {}

ml_charts: List[Dict[str, str]] = []
charts_ml = (ml_outputs.get("charts") or {})
for nice, key in [
    ("Verteilung der Zielgröße (Aufenthaltsdauer)", "target_hist"),
    ("Streuung der Merkmale (IQR-Übersicht)", "iqr_spread"),
    ("Modellvergleich (Cross-Validation RMSE)", "cv_rmse"),
    ("Vorhersage vs. Realität", "pred_vs_true"),
    ("Residuen (Fehler) vs. Vorhersage", "residuals"),
    ("Residuen-Verteilung", "residual_hist"),
]:
    p = charts_ml.get(key)
    if p and file_exists(p):
        ml_charts.append({"title": nice, "path": p})

# ------------------------------------------------------------
# 5) JSON Summary (Klartext)
# ------------------------------------------------------------

final_summary: Dict[str, Any] = {
    "run_id": RUN_ID,  # ✅ zusätzlich maschinenlesbar
    "Run-ID": RUN_ID,  # ✅ und menschlich (wie vorher)
    "Zeitpunkt (UTC)": RUN_TS.isoformat(),
    "Datenbank": DB_PATH.as_posix(),
    "Pipeline vollständig erfolgreich": overall_success,
    "Fehlende Phasen": missing,
    "Pipeline-Status": pipeline_status,
    "Hauptergebnisse": {
        "ADaM-Qualität": {
            "Anzahl kritischer Auffälligkeiten": adam_hard_fail_count,
            "Strenge Qualitäts-Schranke aktiv": adam_strict,
        },
        "Analyse-Datensatz (Marts)": {
            "Anzahl eindeutiger Patienten (Union aller Quellen)": (
                marts_keycheck.get("union_keys") if isinstance(marts_keycheck, dict) else None
            ),
            "Anzahl Patienten im finalen Analyse-Datensatz": (
                marts_keycheck.get("merged_keys") if isinstance(marts_keycheck, dict) else None
            ),
            **(marts_metrics if isinstance(marts_metrics, dict) else {}),
            "Erzeugte Mart-Tabellen": list(marts_tables.keys()) if isinstance(marts_tables, dict) else marts_tables,
        },
        "Modelltraining": {
            "Bestes Modell": ml_best_model,
            "Bestes Feature-Set": ml_best_dataset,
            "Holdout-Ergebnis (Testdaten)": ml_holdout,
        },
    },
    "Artefakte": {
        "ADaM QC Report": (adam_outputs.get("html_report") if isinstance(adam_outputs, dict) else None),
        "ML Report": (ml_outputs.get("html_report") if isinstance(ml_outputs, dict) else None),
    },
}

FINAL_JSON.write_text(json.dumps(final_summary, indent=2, ensure_ascii=False), encoding="utf-8")

# ------------------------------------------------------------
# 6) HTML Report (lesbar + Bilder)
# ------------------------------------------------------------

css = """
<style>
  body { font-family: Arial, sans-serif; margin: 24px; line-height: 1.4; }
  h1,h2,h3 { margin: 10px 0 6px 0; }
  .meta { color: #333; margin-bottom: 14px; }
  .pill-ok { display: inline-block; padding: 3px 10px; border-radius: 999px; background: #e9f7ef; color: #1e7e34; font-weight: 700; }
  .pill-fail { display: inline-block; padding: 3px 10px; border-radius: 999px; background: #fdeaea; color: #b02a37; font-weight: 700; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .card { border: 1px solid #ddd; border-radius: 12px; padding: 14px; background: #fff; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0 14px 0; }
  th, td { border: 1px solid #ddd; padding: 8px; font-size: 13px; vertical-align: top; }
  th { background: #f3f3f3; text-align: left; }
  code { background: #f7f7f7; padding: 2px 4px; border-radius: 3px; }
  img { max-width: 100%; border: 1px solid #ddd; border-radius: 10px; }
  .small { color: #555; font-size: 13px; }
  ul { margin: 6px 0 12px 18px; }
  .section-note { color: #444; background: #fafafa; padding: 10px 12px; border: 1px solid #eee; border-radius: 10px; }
</style>
"""

status_pill = (
    '<span class="pill-ok">Erfolgreich</span>'
    if overall_success
    else '<span class="pill-fail">Unvollständig / Fehler</span>'
)

pipeline_table = html_table(pipeline_status, empty_msg="Keine Statusdaten.")

# ADaM summary (friendly)
adam_summary_rows: List[Dict[str, Any]] = []
if adam_hard_fail_count is not None:
    adam_summary_rows.append(
        {
            "Kennzahl": "Kritische Auffälligkeiten (Hard Fails)",
            "Wert": nice_metric(adam_hard_fail_count, digits=2),
            "Interpretation": "0 bedeutet: keine harten Qualitätsverletzungen.",
        }
    )
if adam_strict is not None:
    adam_summary_rows.append(
        {
            "Kennzahl": "Strenge Qualitäts-Schranke aktiv",
            "Wert": nice_metric(bool(adam_strict)),
            "Interpretation": "Wenn aktiv, würde der Lauf bei kritischen Fehlern abbrechen.",
        }
    )
adam_summary_table = html_table(adam_summary_rows, empty_msg="Keine ADaM-QC-Zusammenfassung verfügbar.")

# Marts summary
marts_summary_rows: List[Dict[str, Any]] = []

uk = marts_keycheck.get("union_keys") if isinstance(marts_keycheck, dict) else None
mk = marts_keycheck.get("merged_keys") if isinstance(marts_keycheck, dict) else None
if uk is not None:
    marts_summary_rows.append({"Kennzahl": "Anzahl eindeutiger Patienten (Union aller Quellen)", "Wert": uk})
if mk is not None:
    marts_summary_rows.append({"Kennzahl": "Anzahl Patienten im Analyse-Datensatz", "Wert": mk})
if isinstance(uk, (int, float)) and isinstance(mk, (int, float)) and uk:
    marts_summary_rows.append({"Kennzahl": "Abdeckung (Analyse vs. Union)", "Wert": f"{round((mk/uk)*100.0, 2)}%"})

if isinstance(marts_metrics, dict) and marts_metrics:
    for k, v in marts_metrics.items():
        marts_summary_rows.append({"Kennzahl": str(k), "Wert": nice_metric(v)})

if isinstance(marts_tables, dict) and marts_tables:
    marts_summary_rows.append({"Kennzahl": "Erzeugte Mart-Tabellen", "Wert": ", ".join(marts_tables.keys())})

marts_summary_table = html_table(marts_summary_rows, empty_msg="Keine Marts-Kennzahlen verfügbar.")

# ML summary
ml_summary_rows: List[Dict[str, Any]] = []
if ml_best_model:
    ml_summary_rows.append({"Kennzahl": "Ausgewähltes Modell", "Wert": ml_best_model})
if ml_best_dataset:
    ml_summary_rows.append({"Kennzahl": "Ausgewähltes Feature-Set", "Wert": ml_best_dataset})

metric_label_map = {"mae": "MAE", "rmse": "RMSE", "r2": "R²", "n_test": "n (Test)"}
if isinstance(ml_holdout, dict) and ml_holdout:
    for k, v in ml_holdout.items():
        ml_summary_rows.append({"Kennzahl": metric_label_map.get(k, str(k)), "Wert": nice_metric(v)})

ml_summary_table = html_table(ml_summary_rows, empty_msg="Keine ML-Kennzahlen verfügbar.")

# Detail-Links
extra_links = []
adam_html = adam_outputs.get("html_report") if isinstance(adam_outputs, dict) else None
ml_html = ml_outputs.get("html_report") if isinstance(ml_outputs, dict) else None

if adam_html and file_exists(adam_html):
    extra_links.append(f"<li>Detailreport ADaM-Qualität: <code>{escape(Path(adam_html).name)}</code></li>")
if ml_html and file_exists(ml_html):
    extra_links.append(f"<li>Detailreport Modelltraining: <code>{escape(Path(ml_html).name)}</code></li>")

links_html = "<ul>" + "\n".join(extra_links) + "</ul>" if extra_links else "<p><em>(keine Detail-HTMLs gefunden)</em></p>"

html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Abschlussbericht – {escape(RUN_ID)}</title>
{css}
</head>
<body>

<h1>Abschlussbericht: Datenpipeline & Modellierung</h1>

<div class="meta">
  <div><b>Run-ID</b>: <code>{escape(RUN_ID)}</code></div>
  <div><b>Zeitpunkt (UTC)</b>: {escape(RUN_TS.isoformat())}</div>
  <div><b>Datenbank</b>: <code>{escape(DB_PATH.as_posix())}</code></div>
  <div><b>Gesamtstatus</b>: {status_pill}</div>
</div>

<div class="section-note">
  <b>Ziel</b>: Aufbau einer vollständigen Verarbeitungskette von Rohdaten bis zur Vorhersage der Aufenthaltsdauer
  (inkl. Qualitätsprüfungen, Standardisierung, Analyse-Datensatz und Modellbewertung).
</div>

<h2>1) Pipeline-Status</h2>
<p class="small">Diese Übersicht zeigt, ob die einzelnen Projektphasen erfolgreich abgeschlossen wurden.</p>
{pipeline_table}

<h2>2) Datenqualität & Standardisierung</h2>
<div class="grid">
  <div class="card">
    <h3>ADaM-Qualität (Analyse-Datensatz-Standard)</h3>
    {adam_summary_table}
  </div>
  <div class="card">
    <h3>Analyse-Datensatz (Marts)</h3>
    {marts_summary_table}
  </div>
</div>

<h2>3) Modelltraining & Modellgüte</h2>
<div class="card">
  <h3>Zusammenfassung der Modellleistung (Testdaten)</h3>
  {ml_summary_table}
</div>

<h2>4) Grafiken</h2>
<h3>ADaM Qualitätsgrafiken</h3>
{render_charts(adam_qc_charts)}

<h3>Modelltraining Grafiken</h3>
{render_charts(ml_charts)}

<h2>5) Detail-Reports & Artefakte</h2>
{links_html}

</body>
</html>
"""

FINAL_HTML.write_text(html_doc, encoding="utf-8")

# ------------------------------------------------------------
# 7) Console summary
# ------------------------------------------------------------
print("\n" + "-" * 70)
print("FINAL REPORT – SUMMARY")
print("-" * 70)
print(f"[run] run_id               : {RUN_ID}")
print(f"[out] summary json         : {FINAL_JSON}")
print(f"[out] final report html    : {FINAL_HTML}")
print(f"[ok ] overall success      : {overall_success}")
if missing:
    print(f"[warn] missing phases      : {', '.join(missing)}")
print("=" * 70 + "\n")
print("STEP 12 DONE")
