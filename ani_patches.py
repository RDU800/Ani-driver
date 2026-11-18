#!/usr/bin/env python3
"""
ani_patches.py - Applies Mesa libpoly geometry & tessellation patches for PanVK.
"""
import os
import pathlib
from ani_diag import log

RECOMMENDED_REF = "mesa-26.3.0"

def apply_patches():
    log(f"Applying Mesa patches for geometry/tessellation (Target ref: {RECOMMENDED_REF})")
    mesa_dir = pathlib.Path(os.environ.get("MESA_SRC_DIR", "./mesa")).resolve()
    
    if not (mesa_dir / "meson.build").exists():
        log(f"Mesa source directory not found at {mesa_dir}", "ERROR")
        return

    poly_dir = mesa_dir / "src" / "poly"
    if poly_dir.exists():
        log(f"Found libpoly source tree at {poly_dir}", "OK")
    else:
        log(f"src/poly missing. Ensure Mesa is checked out to {RECOMMENDED_REF}.", "WARN")

    # Ensure device feature support for geometry and tessellation shaders
    panvk_c = mesa_dir / "src" / "panfrost" / "vulkan" / "panvk_physical_device.c"
    if panvk_c.exists():
        content = panvk_c.read_text(encoding="utf-8")
        if ".geometryShader = true" in content:
            log("geometryShader already enabled in panvk_physical_device.c", "OK")
        elif ".robustBufferAccess =" in content:
            patched = content.replace(
                ".robustBufferAccess =",
                ".geometryShader = true,\n      .tessellationShader = true,\n      .robustBufferAccess =",
                1,
            )
            panvk_c.write_text(patched, encoding="utf-8")
            log("Successfully injected geometry and tessellation feature flags into PanVK device.", "PATCHED")
    else:
        log("panvk_physical_device.c not found; proceeding with upstream defaults.", "INFO")

if __name__ == "__main__":
    apply_patches()
    
