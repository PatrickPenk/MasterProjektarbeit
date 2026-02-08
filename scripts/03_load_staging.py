from __future__ import annotations

import json
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import duckdb

from scripts.config import DB_PATH, OUT_DIR, ensure_dirs, CORE_TABLES

ensure_dirs()

print("\n" + "=" * 60)
print("STEP 03 - LOAD STAGING")
print("=" * 60)

# ============================================================
# 1. Manifest laden 
# ============================================================

manifest_candidates = [
    OUT_DIR / "manifest_raw_profiled.json",
    OUT_DIR / "manifest_raw.json",
]

manifest_in_path = next((p for p in manifest_candidates if p.exists()), None)
if manifest_in_path is None:
    raise FileNotFoundError(
        "Kein Manifest gefunden. Erwartet:\n"
        f" - {manifest_candidates[0]}\n"
        f" - {manifest_candidates[1]}\n"
        "Bitte zuerst Step 01 (und optional Step 02) ausführen."
    )

manifest_in = json.loads(manifest_in_path.read_text(encoding="utf-8"))
csv_paths: Dict[str, Path] = {k: Path(v) for k, v in manifest_in["csv_paths"].items()}

run_id = manifest_in.get("run_id") or str(uuid.uuid4())

print(f"[in] manifest            : {manifest_in_path}")
print(f"[run] run_id             : {run_id}")
print(f"[db] path                : {DB_PATH}")

# ============================================================
# 2. Funktionen Definieren
# ============================================================

# Integrität 
def sha256_file(path: Path, chunk_size: int = 2**20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

# Datenbank erstellen
def open_duckdb(db_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path))
    con.execute("PRAGMA threads=4")
    return con

# Metafiles anlegen
def ensure_meta_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS meta_runs (
        run_id VARCHAR PRIMARY KEY,
        started_at TIMESTAMP,
        manifest_in VARCHAR,
        db_path VARCHAR
    );
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS meta_stg_load (
        run_id VARCHAR,
        table_name VARCHAR,
        stg_table VARCHAR,
        source_file VARCHAR,
        source_sha256 VARCHAR,
        ingested_at TIMESTAMP,
        row_count BIGINT,
        status VARCHAR,
        message VARCHAR
    );
    """)

# Daten in Datenbank laden
def duckdb_load_staging(
    con: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    csv_paths: Dict[str, Path],
    tables: List[str],
) -> Dict[str, int]:
    rowcounts: Dict[str, int] = {}
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)

    for t in tables:
        stg_name = f"stg_{t}"

        if t not in csv_paths:
            con.execute(
                "INSERT INTO meta_stg_load VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, t, stg_name, None, None, ingested_at, None, "SKIP", "missing CSV"]
            )
            print(f"[duckdb][load] skip missing CSV: {t}")
            continue

        path = csv_paths[t]
        src_sha = sha256_file(path)

        try:
            con.execute(
                f"""
                CREATE OR REPLACE TABLE {stg_name} AS
                SELECT
                    *,
                    ?::VARCHAR   AS _run_id,
                    ?::TIMESTAMP AS _ingested_at,
                    ?::VARCHAR   AS _source_file
                FROM read_csv_auto('{path.as_posix()}', HEADER=TRUE, SAMPLE_SIZE=-1)
                """,
                [run_id, ingested_at, path.name]
            )

            n = con.execute(f"SELECT COUNT(*) FROM {stg_name}").fetchone()[0]
            rowcounts[t] = int(n)

            con.execute(
                "INSERT INTO meta_stg_load VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, t, stg_name, path.as_posix(), src_sha, ingested_at, int(n), "OK", None]
            )

            print(f"[duckdb][load] {stg_name}: {n:,} rows")

        except Exception as e:
            con.execute(
                "INSERT INTO meta_stg_load VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, t, stg_name, path.as_posix(), src_sha, ingested_at, None, "ERROR", str(e)]
            )
            raise

    return rowcounts

# ============================================================
# 3. Ausführen
# ============================================================

tables_to_load = [t for t in CORE_TABLES if t in csv_paths]
# tables_to_load = sorted(csv_paths.keys())

print(f"[plan] tables_to_load    : {', '.join(tables_to_load) if tables_to_load else '(none)'}")

con = open_duckdb(DB_PATH)
try:
    ensure_meta_tables(con)

    con.execute(
        "INSERT OR REPLACE INTO meta_runs VALUES (?, now(), ?, ?)",
        [run_id, str(manifest_in_path), DB_PATH.as_posix()]
    )

    rowcounts = duckdb_load_staging(
        con,
        run_id=run_id,
        csv_paths=csv_paths,
        tables=tables_to_load,
    )
finally:
    con.close()

# ============================================================
# 4. Manifest updaten
# ============================================================

manifest_out = dict(manifest_in)
manifest_out["run_id"] = run_id
manifest_out.update({
    "duckdb": {
        "db_path": DB_PATH.as_posix(),
        "staging_prefix": "stg_",
        "tables_loaded": sorted(rowcounts.keys()),
        "rowcounts": rowcounts,
        "meta_tables": ["meta_runs", "meta_stg_load"],
    }
})

manifest_out_path = OUT_DIR / "manifest_staging.json"
manifest_out_path.write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

print("\n" + "-" * 60)
print("STAGING LOAD - SUMMARY")
print("-" * 60)
print(f"[out] manifest            : {manifest_out_path}")
print(f"[out] loaded_tables       : {len(rowcounts)}")
if rowcounts:
    top3 = sorted(rowcounts.items(), key=lambda x: x[1], reverse=True)[:3]
    print("[out] largest_tables      : " + ", ".join([f"{t}({n:,})" for t, n in top3]))

print("=" * 60 + "\n")
