# ============================================================
# run_pipeline.py
# One-click runner: führt alle Steps nacheinander aus (als Module).
#
# Erwartung: Deine Steps liegen als Python-Module unter scripts/
# z.B. scripts/01_download_extract.py -> python -m scripts.01_download_extract
#
# Enthält jetzt: Steps 01–13 (inkl. Marts, ML, Final Report, Archivpaket)
# ============================================================

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Step:
    name: str
    module: str
    optional: bool = False


# Reihenfolge (anpassen, falls deine Dateinamen abweichen)
STEPS: List[Step] = [
    Step("Step 01 - Download/Extract", "scripts.01_download_extract"),
    Step("Step 02 - Raw Profiling", "scripts.02_raw_profiling"),
    Step("Step 03 - Load Staging", "scripts.03_load_staging"),
    Step("Step 04 - Staging DQ Checks", "scripts.04_staging_qc"),
    Step("Step 05 - Curated Transform", "scripts.05_curated_transform"),
    Step("Step 06 - SDTM Mapping", "scripts.06_sdtm_mapping"),
    Step("Step 07 - SDTM Mapping QC", "scripts.07_sdtm_mapping_qc"),
    Step("Step 08 - ADaM Mapping", "scripts.08_adam_mapping"),
    Step("Step 09 - ADaM QC", "scripts.09_adam_mapping_qc"),
    Step("Step 10 - Marts Build", "scripts.10_marts"),
    Step("Step 11 - ML Training", "scripts.11_ml_train"),
    Step("Step 12 - Final Report", "scripts.12_final_summary"),
    Step("Step 13 - Archive Package", "scripts.13_archive"),
]


def run_step(step: Step) -> int:
    cmd = [sys.executable, "-m", step.module]
    print("\n" + "=" * 80)
    print(f"RUN: {step.name}")
    print(f"CMD: {' '.join(cmd)}")
    print("=" * 80)

    try:
        res = subprocess.run(cmd, check=True)
        return res.returncode
    except subprocess.CalledProcessError as e:
        print("\n" + "!" * 80)
        print(f"FAILED: {step.name}")
        print(f"Module: {step.module}")
        print(f"Exit code: {e.returncode}")
        print("!" * 80)
        return e.returncode
    except ModuleNotFoundError:
        print("\n" + "!" * 80)
        print(f"SKIP (module not found): {step.module}")
        print("!" * 80)
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or sys.argv[1:]

    # optional: allow running from a given step index, e.g. --from 5
    start_idx = 0
    if "--from" in argv:
        i = argv.index("--from")
        if i + 1 >= len(argv):
            print("Usage: --from <step_number>")
            return 2
        start_num = int(argv[i + 1])
        for idx, s in enumerate(STEPS):
            mod = s.module.split(".")[-1]
            if mod.startswith(f"{start_num:02d}_") or mod.startswith(f"{start_num}_"):
                start_idx = idx
                break

    for step in STEPS[start_idx:]:
        rc = run_step(step)
        if rc != 0:
            if step.optional:
                print(f"[warn] optional step failed -> continue: {step.name}")
                continue
            print(f"[stop] pipeline aborted at: {step.name}")
            return rc

    print("\n" + "=" * 80)
    print("PIPELINE DONE ✅")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
