from pathlib import Path
from typing import Dict
import time
import zipfile
import hashlib
import requests
import json

from scripts.config import RAW_DIR, EXTRACT_DIR, OUT_DIR, ensure_dirs
ensure_dirs()

# ============================================================
print("\n" + "=" * 60)
print("STEP 01 - DOWNLOAD & EXTRACT (RAW)")
print("=" * 60)

# ============================================================
# 1. Funktionen Definieren
# ============================================================

# Pfad zur Datei
SYNTHEA_ZIP_URL = (
    "https://synthetichealth.github.io/synthea-sample-data/downloads/latest/"
    "synthea_sample_data_csv_latest.zip"
)

# Integrität der Datei überprüfen
def sha256_file(path: Path, chunk_size: int = 2**20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

# Daten downloaden
def download_file(url: str, out_path: Path, *, overwrite: bool = False, timeout: int = 60) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and not overwrite:
        print(f"[download] exists: {out_path.name} ({out_path.stat().st_size/1e6:.1f} MB)")
        return out_path

    print(f"[download] downloading: {url}")
    with requests.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
        r.raise_for_status()

        tmp = out_path.with_suffix(out_path.suffix + ".part")  # temporäre Datei (atomic rename)
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=2**20):
                if chunk:
                    f.write(chunk)
        tmp.replace(out_path)

    print(f"[download] done: {out_path.name} ({out_path.stat().st_size/1e6:.1f} MB)")
    print(f"[download] sha256: {sha256_file(out_path)[:16]}...")
    return out_path

# CSV Dateien lokalisieren
def locate_csv_root(extracted_dir: Path) -> Path:
    candidates = list(extracted_dir.rglob("*.csv"))
    if not candidates:
        raise FileNotFoundError("Keine CSVs gefunden. Prüfe EXTRACT_DIR.")
    parents = {}
    for p in candidates:
        parents[p.parent] = parents.get(p.parent, 0) + 1
    return max(parents, key=parents.get)

# Unzippen mit ZipSlip Schutz 
def safe_unzip(zip_path: Path, out_dir: Path, *, overwrite: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / ".unzipped.ok"

    if marker.exists() and not overwrite:
        print(f"[unzip] exists: {out_dir}")
        return out_dir

    def is_within(base: Path, target: Path) -> bool:
        base = base.resolve()
        target = target.resolve()
        return str(target).startswith(str(base))

    with zipfile.ZipFile(zip_path, "r") as z:
        for member in z.infolist():
            tgt = out_dir / member.filename
            if not is_within(out_dir, tgt):
                raise RuntimeError(f"Unsafe path in zip (zip slip): {member.filename}")
        z.extractall(out_dir)

    marker.write_text(f"unzipped from {zip_path.name} at {time.ctime()}\n", encoding="utf-8")
    print(f"[unzip] done: {zip_path.name} -> {out_dir}")
    return out_dir

# Wichtige CSVs validieren
def check_csv_root(csv_root: Path, required: set[str]) -> dict:
    found = {p.stem for p in csv_root.glob("*.csv")}
    missing = required - found

    return {
        "csv_root": csv_root,
        "found": sorted(found),
        "missing": sorted(missing),
        "ok": len(missing) == 0,
    }

# ============================================================
# 2. Ausführen
# ============================================================

# Ordner downloaden
zip_path = RAW_DIR / "synthea_sample_data_csv_latest.zip"
zip_path = download_file(SYNTHEA_ZIP_URL, zip_path)

# Integrität überprüfen
zip_sha = sha256_file(zip_path)
(OUT_DIR / "zip_sha256.txt").write_text(zip_sha + "\n", encoding="utf-8")

# Entpacken
extracted_dir = safe_unzip(zip_path, EXTRACT_DIR / "synthea_csv")

# Überprüfen wo CSVs liegen
csv_root = locate_csv_root(extracted_dir)

# validieren wichtiger Dateien
check = check_csv_root(
    csv_root=csv_root,
    required={"patients", "encounters"}
)

# Erstellen von Dict (Datei:Pfad) zu jeder Datei. Wird im nächsten Schritt (DuckDB staging load) verwendet
csv_paths: Dict[str, Path] = {p.stem: p for p in csv_root.glob("*.csv")}

# ============================================================
# 3. Ausgabe
# ============================================================

print("[raw] CSV Root Folder:", check["csv_root"])
print("[raw] zip Ordner sha256:", zip_sha[:16], "...")
print("[raw] Gefundene Tabellen:", len(check["found"]))

if check["missing"]:
    print("[raw][ERROR] Fehlende Kern-CSV(s):", check["missing"])
    raise RuntimeError("CSV-Validierung fehlgeschlagen – siehe Ausgabe oben.")
else:
    print("[raw][OK] Alle erforderlichen CSVs vorhanden.")

# ============================================================
# 4. Manifest
# ============================================================

manifest = {
    "csv_root": csv_root.as_posix(),
    "csv_paths": {k: v.as_posix() for k, v in csv_paths.items()},
    "zip_sha256": zip_sha,
}

manifest_path = OUT_DIR / "manifest_raw.json"
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

print("[manifest] wrote:", manifest_path)
print("=" * 60 + "\n")
