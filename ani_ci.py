#!/usr/bin/env python3
"""
ani_ci.py - Build & Release Orchestrator for ani-driver
Pulls the armsx2-turnip fork (malisx2 branch), applies features, builds, verifies, packages, and publishes.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile

# Ensure unbuffered stdout/stderr so CI logs appear immediately
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

E = os.environ.get
NAME = E("DRIVER_NAME", "ani-driver")
SLUG = E("ZIP_SLUG", "ani-driver")
BUILD_TYPE = E("BUILD_TYPE", "release")
NDK_VERSION = E("NDK_VERSION", "28.2.13676358")
RAW_FEATURES = [f.strip() for f in E("FEATURES", "").split(",") if f.strip()]
# 'geometry' is native in malisx2 and does not require a patch function in ani_features.py
NATIVE_FEATURES = {"geometry"}
FEATURES = [f for f in RAW_FEATURES if f not in NATIVE_FEATURES]

ANDROID_HOME = E("ANDROID_HOME", "/usr/local/lib/android/sdk")
SO = "build-android/src/libvulkan_malisx2.so"
LIB = "libvulkan_malisx2.so"
STAGES = ["number", "patch", "mesa", "tools", "build", "verify", "package", "publish"]

for p in ("~/.cargo/bin", "~/.local/bin"):
    os.environ["PATH"] = os.path.expanduser(p) + os.pathsep + os.environ["PATH"]


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def out(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def tc(tool):
    return "%s/ndk/%s/toolchains/llvm/prebuilt/linux-x86_64/bin/%s" % (ANDROID_HOME, NDK_VERSION, tool)


def next_build_no():
    v = E("BUILD_NO", "auto")
    if v != "auto":
        return int(v)
    try:
        tags = out([
            "gh", "release", "list", "--limit", "200", "--json", "tagName",
            "-q", ".[].tagName"
        ]).split()
    except Exception as e:
        print("could not list releases (%s); using the run number" % e, flush=True)
        return int(E("GITHUB_RUN_NUMBER", "1"))
    nums = [
        int(m.group(1))
        for t in tags
        for m in [re.match(re.escape(SLUG) + r"-build-(\d+)", t)]
        if m
    ]
    return max(nums, default=0) + 1


def report():
    applied, skipped = [], []
    for nf in RAW_FEATURES:
        if nf in NATIVE_FEATURES:
            applied.append(f"{nf} (native)")
    if os.path.exists("ani_features_report.txt"):
        for line in open("ani_features_report.txt").read().splitlines():
            kind, _, rest = line.partition(": ")
            if kind == "applied":
                applied.append(rest)
            elif kind:
                skipped.append(rest)
    return applied, skipped


def stage_patch(n):
    os.environ["BUILD_NO"] = str(n)
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    import ani_features

    for nf in RAW_FEATURES:
        if nf in NATIVE_FEATURES:
            print(f"[ani_ci] '{nf}' is native to malisx2; skipping patcher.", flush=True)

    if FEATURES:
        ani_features.apply(FEATURES)
    else:
        print("[ani_ci] No non-native patch features to apply.", flush=True)


def stage_mesa(n):
    if os.path.isdir("mesa/.git"):
        print("[ani_ci] mesa repo already present; skipping clone.", flush=True)
        return
    run([
        "git", "clone", "--depth", "1", "--branch", "malisx2",
        "https://github.com/bmdhacks/armsx2-turnip.git", "mesa"
    ])


def stage_tools(n):
    run(["sudo", "apt-get", "update"])
    run([
        "sudo", "apt-get", "install", "-y",
        "ninja-build", "libclang-dev", "clang", "python3-pip", "zip"
    ])
    run(["pip", "install", "--break-system-packages", "meson==1.7.0", "mako", "pyyaml"])
    run(["rustup", "update", "stable"])
    run(["rustup", "target", "add", "aarch64-linux-android"])
    if not os.path.exists(os.path.expanduser("~/.cargo/bin/bindgen")):
        run(["cargo", "install", "bindgen-cli", "--version", "0.71.1", "--locked"])
    ndk_dir = "%s/ndk/%s" % (ANDROID_HOME, NDK_VERSION)
    if not os.path.isdir(ndk_dir):
        run([
            "bash", "-c",
            'yes | "%s/cmdline-tools/latest/bin/sdkmanager" "ndk;%s" >/dev/null'
            % (ANDROID_HOME, NDK_VERSION)
        ])


def stage_build(n):
    ndk = "%s/ndk/%s" % (ANDROID_HOME, NDK_VERSION)
    cross_path = "cross/android-aarch64.ini"
    if not os.path.exists(cross_path):
        cross_path = "android-aarch64-cross.ini"

    if os.path.exists(cross_path):
        cross = open(cross_path).read()
        cross = re.sub(r"(?m)^ndk = .*$", "ndk = '%s'" % ndk, cross)
        open("cross-ci.ini", "w").write(cross)
        cross_file = "cross-ci.ini"
    else:
        cross_file = cross_path

    run([
        "meson", "setup", "build-android", "mesa",
        "--cross-file", cross_file,
        "--buildtype=" + BUILD_TYPE,
        "-Dplatforms=android",
        "-Dplatform-sdk-version=29",
        "-Dandroid-stub=true",
        "-Dgallium-drivers=",
        "-Dvulkan-drivers=panfrost",
        "-Dallow-fallback-for=libdrm",
        "-Degl=disabled",
        "-Dgles1=disabled",
        "-Dgles2=disabled",
        "-Dglx=disabled",
        "-Dllvm=enabled",
        "-Dshared-llvm=disabled",
        "-Dshader-cache=enabled",
        "-Dzstd=disabled",
    ])
    run(["ninja", "-C", "build-android"])


def stage_verify(n):
    bad = []
    if not os.path.isfile(SO):
        sys.exit("FAIL: %s not found" % SO)
    if "AArch64" not in out([tc("llvm-readelf"), "-h", SO]):
        bad.append("not an AArch64 library")
    needed = re.findall(r"Shared library: \[(.*?)\]", out([tc("llvm-readelf"), "-d", SO]))
    print("needed libraries:", needed, flush=True)
    bad += [
        "unexpected dependency " + l
        for l in needed
        if l not in ("liblog.so", "libm.so", "libdl.so", "libc.so")
    ]
    exports = out([tc("llvm-nm"), "-D", "--defined-only", SO]).split()
    for sym in (
        "HMI",
        "vk_icdGetInstanceProcAddr",
        "vk_icdGetPhysicalDeviceProcAddr",
        "vk_icdNegotiateLoaderICDInterfaceVersion",
    ):
        if sym not in exports:
            bad.append("missing export " + sym)
    if ("ani-driver-build%d" % n).encode() not in open(SO, "rb").read():
        bad.append("build label missing")
    if bad:
        sys.exit("VERIFY FAILED: " + "; ".join(bad))
    print("VERIFY OK", flush=True)


def zip_name(n):
    return "%s-build-%d.zip" % (SLUG, n)


def stage_package(n):
    os.makedirs("out", exist_ok=True)
    run([tc("llvm-strip"), "-o", "out/" + LIB, SO])
    meta = {
        "schemaVersion": 1,
        "name": "%s build %d" % (NAME, n),
        "description": "%s build %d: malisx2-based Vulkan driver for Mali G52 (kbase), for emulators" % (NAME, n),
        "author": NAME + " (fork of malisx2 by bmdhacks)",
        "packageVersion": str(n),
        "vendor": NAME,
        "driverVersion": "44.1.0",
        "minApi": 29,
        "libraryName": LIB,
    }
    json.dump(meta, open("out/meta.json", "w"), indent=2)
    with zipfile.ZipFile(zip_name(n), "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write("out/" + LIB, LIB)
        z.write("out/meta.json", "meta.json")
    digest = hashlib.sha256(open(zip_name(n), "rb").read()).hexdigest()
    open("SHA256SUMS.txt", "w").write("%s  %s\n" % (digest, zip_name(n)))
    size = os.path.getsize(zip_name(n)) / 1048576
    applied, skipped = report()
    summary = [
        "## %s build %d (%s)" % (NAME, n, BUILD_TYPE),
        "",
        "- zip: `%s` (%.1f MB)" % (zip_name(n), size),
        "- sha256: `%s`" % digest,
        "- features applied: " + (", ".join(applied) or "none"),
        "- features skipped: " + ("; ".join(skipped) or "none"),
        "- library check passed",
    ]
    print("\n".join(summary), flush=True)
    if E("GITHUB_STEP_SUMMARY"):
        open(E("GITHUB_STEP_SUMMARY"), "a").write("\n".join(summary) + "\n")


def stage_publish(n):
    tag = "%s-build-%d-run%s" % (SLUG, n, E("GITHUB_RUN_NUMBER", "0"))
    applied, skipped = report()
    notes = (
        "%s build %d (%s build). Features applied: %s. Skipped: %s. Commit %s."
        % (
            NAME,
            n,
            BUILD_TYPE,
            ", ".join(applied) or "none",
            "; ".join(skipped) or "none",
            E("GITHUB_SHA", "?"),
        )
    )
    run([
        "gh", "release", "create", tag, zip_name(n), "SHA256SUMS.txt",
        "--prerelease",
        "--title", "%s build %d" % (NAME, n),
        "--notes", notes,
    ])


def main():
    wanted = sys.argv[1:] or STAGES
    for w in wanted:
        if w not in STAGES:
            sys.exit("Unknown stage '%s'. Stages: %s" % (w, " ".join(STAGES)))
    n = next_build_no()
    print("::notice::%s build %d" % (NAME, n), flush=True)
    for s in STAGES:
        if s in wanted and s != "number":
            print("::group::" + s, flush=True)
            globals()["stage_" + s](n)
            print("::endgroup::", flush=True)


if __name__ == "__main__":
    main()
        
