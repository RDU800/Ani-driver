#!/usr/bin/env python3
"""
ani_ci.py - Minimal Android Vulkan Driver Build Orchestrator
Handles NDK setup, Meson upgrade, Mesa compilation, patching, and delegates packaging/publishing to ani_release.
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

MESA_REPO_URL = os.environ.get("MESA_REPO_URL", "https://gitlab.freedesktop.org/mesa/mesa.git")
MESA_REF = os.environ.get("MESA_REF", "mesa-26.3.0")


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

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
"""
    cross_file.write_text(content, encoding="utf-8")
    log(f"Wrote Meson Android cross-file to {cross_file}", "OK")
    return cross_file


def main():
    log("=== STARTING ANI-DRIVER BUILD PIPELINE ===")
    check_memory()

    # Ensure Meson meets Mesa 26.3.0 requirement (>= 1.5.0)
    log("Upgrading Meson to satisfy Mesa requirements...", "INFO")
    run_cmd(["pip", "install", "--upgrade", "meson"])

    build_type = os.environ.get("BUILD_TYPE", "release")
    ndk_version = os.environ.get("NDK_VERSION", "28.2.13676358")
    features_raw = os.environ.get("FEATURES", "")
    features = [f.strip() for f in features_raw.split(",") if f.strip()]

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
    log(f"Found compiled driver binary: {compiled_so}", "OK")

    # 4. Delegate to static release layer
    ani_release.package_and_publish(compiled_so, MESA_REF, features)


if __name__ == "__main__":
    main()
    
