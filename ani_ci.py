#!/usr/bin/env python3
"""
ani_ci.py - Build Orchestrator for Meson Setup and Compilation
"""
import os
import subprocess
import pathlib
import ani_patches
import ani_features
from ani_diag import log, check_memory

def run_ci():
    log("=== STARTING ANI-DRIVER CI BUILD ORCHESTRATION ===")
    check_memory()

    # 1. Apply features and patches prior to build setup
    ani_features.configure_features()
    ani_patches.apply_patches()

    mesa_dir = pathlib.Path(os.environ.get("MESA_SRC_DIR", "./mesa")).resolve()
    build_dir = mesa_dir / "build"

    # Required Meson flags for libpoly compute geometry lowering & host mesa_clc
    meson_flags = [
        "-Dpoly=enabled",
        "-Dmesa-clc=enabled",
        "-Dinstall-mesa-clc=true",
        "-Dspirv-tools=enabled",
    ]

    log(f"Meson geometry & compilation flags: {' '.join(meson_flags)}")
    
    # Save flags for downstream build steps if needed
    flags_file = pathlib.Path("extra_meson_flags.txt")
    flags_file.write_text(" ".join(meson_flags) + "\n", encoding="utf-8")
    log(f"Saved build flags to {flags_file}", "DONE")
    log("CI orchestration preparation completed successfully. Proceed to compilation and packaging.")

if __name__ == "__main__":
    run_ci()
  
