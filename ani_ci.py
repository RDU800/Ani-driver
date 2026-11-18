#!/usr/bin/env python3
"""
ani_ci.py - Complete Android Vulkan Driver Build, Packaging, and GitHub Release Orchestrator
Handles NDK setup, Mesa 26.3.0 compilation with feature patches, AdrenoTools .zip packaging with meta.json,
and automatic versioned GitHub Release publishing.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

import ani_patches
import ani_features
from ani_diag import log, check_memory

MESA_REPO_URL = os.environ.get("MESA_REPO_URL", "https://gitlab.freedesktop.org/mesa/mesa.git")
MESA_REF = os.environ.get("MESA_REF", "mesa-26.3.0")


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


def setup_ndk(ndk_version: str) -> pathlib.Path:
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "/usr/local/lib/android/sdk"
    ndk_dir = pathlib.Path(android_home) / "ndk" / ndk_version
    if ndk_dir.exists():
        log(f"Found pre-installed Android NDK at {ndk_dir}", "OK")
        return ndk_dir

    sdkmanager = pathlib.Path(android_home) / "cmdline-tools" / "latest" / "bin" / "sdkmanager"
    if sdkmanager.exists():
        log(f"Installing Android NDK {ndk_version} via sdkmanager...", "INFO")
        run_cmd([str(sdkmanager), f"ndk;{ndk_version}"])
        if ndk_dir.exists():
            return ndk_dir

    work_ndk = pathlib.Path.cwd() / "android-ndk-r28b"
    if not work_ndk.exists():
        zip_name = "android-ndk-r28b-linux.zip"
        url = f"https://dl.google.com/android/repository/{zip_name}"
        log(f"Downloading NDK from {url}...", "INFO")
        urllib.request.urlretrieve(url, zip_name)
        with zipfile.ZipFile(zip_name, "r") as zf:
            zf.extractall(".")
    return work_ndk


def create_android_cross_file(ndk_dir: pathlib.Path, api_level: int = 29) -> pathlib.Path:
    toolchain_bin = ndk_dir / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64" / "bin"
    cross_file = pathlib.Path.cwd() / "android-aarch64-cross.ini"

    content = f"""[binaries]
ar = '{toolchain_bin}/llvm-ar'
c = ['ccache', '{toolchain_bin}/aarch64-linux-android{api_level}-clang']
cpp = ['ccache', '{toolchain_bin}/aarch64-linux-android{api_level}-clang++']
c_ld = 'lld'
cpp_ld = 'lld'
strip = '{toolchain_bin}/llvm-strip'
pkg-config = '/usr/bin/pkg-config'

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
"""
    cross_file.write_text(content, encoding="utf-8")
    log(f"Wrote Meson Android cross-file to {cross_file}", "OK")
    return cross_file


def build_and_publish():
    log("=== STARTING ANI-DRIVER FULL BUILD, ZIP & RELEASE PIPELINE ===")
    check_memory()

    driver_name = os.environ.get("DRIVER_NAME", "ani-driver")
    zip_slug = os.environ.get("ZIP_SLUG", "ani-driver")
    build_no_env = os.environ.get("BUILD_NO", "auto")
    build_type = os.environ.get("BUILD_TYPE", "release")
    ndk_version = os.environ.get("NDK_VERSION", "28.2.13676358")
    features_raw = os.environ.get("FEATURES", "")
    features = [f.strip() for f in features_raw.split(",") if f.strip()]

    build_num = get_next_build_number(zip_slug, build_no_env)
    version_tag = f"v{build_num}"
    zip_filename = f"{zip_slug}-{build_num}-{build_type}.zip"

    # 1. Configure features & clone/patch Mesa
    ani_features.configure_features()

    mesa_dir = pathlib.Path.cwd() / "mesa"
    if not mesa_dir.exists():
        log(f"Cloning Mesa ({MESA_REF})...", "INFO")
        run_cmd(["git", "clone", "--depth", "1", "--branch", MESA_REF, MESA_REPO_URL, str(mesa_dir)])

    os.environ["MESA_SRC_DIR"] = str(mesa_dir)
    ani_patches.apply_patches()

    # 2. Setup NDK & Cross-compilation
    ndk_dir = setup_ndk(ndk_version)
    cross_file = create_android_cross_file(ndk_dir)

    build_dir = mesa_dir / "build-android-aarch64"
    if build_dir.exists():
        shutil.rmtree(build_dir)

    meson_cmd = [
        "meson",
        "setup",
        str(build_dir),
        str(mesa_dir),
        f"--cross-file={cross_file}",
        f"--buildtype={build_type}",
        "-Dplatforms=android",
        "-Dplatform-sdk-version=29",
        "-Dandroid-stub=true",
        "-Dgallium-drivers=",
        "-Dvulkan-drivers=panfrost",
        "-Degl=disabled",
        "-Dgles1=disabled",
        "-Dgles2=disabled",
        "-Dglx=disabled",
        "-Dllvm=disabled",
        "-Dshared-llvm=disabled",
        "-Dshader-cache=enabled",
        "-Dzstd=disabled",
    ]

    run_cmd(meson_cmd)
    run_cmd(["ninja", "-C", str(build_dir)])

    # 3. Locate compiled PanVK Vulkan HAL shared object
    so_candidates = list(build_dir.rglob("libvulkan_panfrost.so"))
    if not so_candidates:
        so_candidates = list(build_dir.rglob("libvulkan_*.so"))
    if not so_candidates:
        raise FileNotFoundError("Compiled Vulkan .so library not found in build output!")

    compiled_so = so_candidates[0]
    target_so_name = "vulkan.panfrost.so"
    log(f"Found compiled driver binary: {compiled_so}", "OK")

    # 4. Package AdrenoTools / Emulator compatible .zip with meta.json
    pkg_dir = pathlib.Path.cwd() / "driver_pkg"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)

    shutil.copy2(compiled_so, pkg_dir / target_so_name)

    meta_data = {
        "schemaVersion": 1,
        "name": f"{driver_name} #{build_num}",
        "description": f"Custom PanVK Mali Vulkan Driver ({MESA_REF}) | Features: {', '.join(features)}",
        "author": "ani-driver",
        "packageVersion": str(build_num),
        "vendor": "ARM (PanVK)",
        "driverVersion": f"{MESA_REF}-{build_num}",
        "minApi": 29,
        "libraryName": target_so_name,
    }
    (pkg_dir / "meta.json").write_text(json.dumps(meta_data, indent=2) + "\n", encoding="utf-8")

    output_zip = pathlib.Path.cwd() / zip_filename
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(pkg_dir / target_so_name, target_so_name)
        zf.write(pkg_dir / "meta.json", "meta.json")

    log(f"Successfully packaged driver archive: {output_zip} ({output_zip.stat().st_size} bytes)", "DONE")

    # 5. Publish GitHub Release automatically with auto-versioned tag
    if os.environ.get("GH_TOKEN"):
        release_title = f"{driver_name} #{build_num} ({MESA_REF})"
        release_notes = (
            f"### {driver_name} Build #{build_num}\n\n"
            f"- **Mesa Base:** `{MESA_REF}`\n"
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


if __name__ == "__main__":
    build_and_publish()
