#!/usr/bin/env python3
"""
ani_release.py - Dedicated packaging and release module for ani-driver.
Handles auto-incremented versioning, meta.json generation, AdrenoTools zip creation,
and GitHub Release publishing. Never needs to be modified for feature changes.
"""
import json
import os
import pathlib
import re
import subprocess
import zipfile
from ani_diag import log


def run_cmd(cmd, cwd=None, env=None):
    log(f"EXEC: {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def get_next_build_number(zip_slug: str, build_no_env: str) -> int:
    if build_no_env.strip().lower() != "auto":
        return int(build_no_env)

    max_num = 0
    try:
        out = subprocess.check_output(["git", "tag", "--list"], text=True)
        for line in out.splitlines():
            m = re.search(r"(\d+)$", line.strip())
            if m:
                max_num = max(max_num, int(m.group(1)))
    except Exception as e:
        log(f"Git tag lookup fallback: {e}", "WARN")

    try:
        out = subprocess.check_output(
            ["gh", "release", "list", "--limit", "50", "--json", "tagName"],
            text=True,
        )
        releases = json.loads(out)
        for r in releases:
            tag = r.get("tagName", "")
            m = re.search(r"(\d+)$", tag)
            if m:
                max_num = max(max_num, int(m.group(1)))
    except Exception as e:
        log(f"GH release lookup note: {e}", "INFO")

    next_num = max_num + 1
    log(f"Auto-resolved next driver build version: #{next_num}", "INFO")
    return next_num


def package_and_publish(compiled_so_path: pathlib.Path, mesa_ref: str, features: list):
    log("=== STARTING DEDICATED RELEASE & PACKAGING PIPELINE ===")
    driver_name = os.environ.get("DRIVER_NAME", "ani-driver")
    zip_slug = os.environ.get("ZIP_SLUG", "ani-driver")
    build_no_env = os.environ.get("BUILD_NO", "auto")
    build_type = os.environ.get("BUILD_TYPE", "release")

    build_num = get_next_build_number(zip_slug, build_no_env)
    version_tag = f"v{build_num}"
    zip_filename = f"{zip_slug}-{build_num}-{build_type}.zip"

    pkg_dir = pathlib.Path.cwd() / "driver_pkg"
    if pkg_dir.exists():
        import shutil
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)

    target_so_name = "vulkan.panfrost.so"
    import shutil
    shutil.copy2(compiled_so_path, pkg_dir / target_so_name)

    meta_data = {
        "schemaVersion": 1,
        "name": f"{driver_name} #{build_num}",
        "description": f"Custom PanVK Mali Vulkan Driver ({mesa_ref}) | Features: {', '.join(features)}",
        "author": "ani-driver",
        "packageVersion": str(build_num),
        "vendor": "ARM (PanVK)",
        "driverVersion": f"{mesa_ref}-{build_num}",
        "minApi": 29,
        "libraryName": target_so_name,
    }
    (pkg_dir / "meta.json").write_text(json.dumps(meta_data, indent=2) + "\n", encoding="utf-8")

    output_zip = pathlib.Path.cwd() / zip_filename
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(pkg_dir / target_so_name, target_so_name)
        zf.write(pkg_dir / "meta.json", "meta.json")

    log(f"Successfully packaged driver archive: {output_zip} ({output_zip.stat().st_size} bytes)", "DONE")

    if os.environ.get("GH_TOKEN"):
        release_title = f"{driver_name} #{build_num} ({mesa_ref})"
        release_notes = (
            f"### {driver_name} Build #{build_num}\n\n"
            f"- **Mesa Base:** `{mesa_ref}`\n"
            f"- **Build Type:** `{build_type}`\n"
            f"- **Enabled Features:** `{', '.join(features)}`\n"
            f"- **Package:** `{zip_filename}`\n"
        )
        log(f"Publishing GitHub Release {version_tag}...", "INFO")
        run_cmd([
            "gh", "release", "create", version_tag,
            str(output_zip),
            "--title", release_title,
            "--notes", release_notes,
        ])
        log(f"Published GitHub Release {version_tag} with {zip_filename}!", "DONE")
    else:
        log("GH_TOKEN not set; skipping GitHub Release upload.", "WARN")
