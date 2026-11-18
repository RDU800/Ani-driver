#!/usr/bin/env python3
"""
ani_ci.py - Minimal Android Vulkan Driver Build Orchestrator
Handles NDK setup, Meson upgrade, cross-file target definition, Mesa compilation, patching, and delegates packaging/publishing to ani_release.
"""
import os
import pathlib
import shutil
import subprocess
import urllib.request
import zipfile

import ani_patches
import ani_features
import ani_release
from ani_diag import log, check_memory

MESA_REPO_URL = os.environ.get("MESA_REPO_URL", "https://github.com/bmdhacks/armsx2-turnip.git")
MESA_REF = os.environ.get("MESA_REF", "malisx2")


def run_cmd(cmd, cwd=None, env=None):
    log(f"EXEC: {' '.join(str(c) for c in cmd)}")
    try:
        subprocess.run(cmd, cwd=cwd, env=env, check=True)
    except subprocess.CalledProcessError as e:
        log(f"Command failed with exit code {e.returncode}", "ERROR")
        build_dir = pathlib.Path.cwd() / "mesa" / "build-android-aarch64"
        log_path = build_dir / "meson-logs" / "meson-log.txt"
        if log_path.exists():
            print("\n--- MESON LOG ERROR DUMP ---")
            print(log_path.read_text(encoding="utf-8", errors="ignore")[-6000:])
            print("--- END MESON LOG ---")
        raise


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
c = '{toolchain_bin}/aarch64-linux-android{api_level}-clang'
cpp = '{toolchain_bin}/aarch64-linux-android{api_level}-clang++'
c_ld = 'lld'
cpp_ld = 'lld'
strip = '{toolchain_bin}/llvm-strip'
pkg-config = '/usr/bin/pkg-config'

[built-in options]
    
