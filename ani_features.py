"""Feature patches for ani-driver (malisx2). Called by ani_ci.py.
Dynamically handles feature toggling, experimental A/B flags, and safe fallbacks
for PS2, Vita, Switch, 3DS, Wii U, Dolphin, and PSP."""
import os
import re
import subprocess
import sys

BUILD = os.environ.get("BUILD_NO", "0")
L = lambda *a: "\n".join(a)
_done = set()


class PatchMismatch(Exception):
    pass


_journal = None


def fail(path, what):
    raise PatchMismatch("%s: %s" % (path, what))


def remember(path):
    if _journal is not None and path not in _journal:
        _journal[path] = open(path).read()


def edit(path, pairs):
    s = open(path).read()
    for old, new in pairs:
        if s.count(old) != 1:
            fail(path, "expected exactly one match of: " + old.strip().split("\n")[0])
        s = s.replace(old, new, 1)
    remember(path)
    open(path, "w").write(s)
    print("patched:", path)


def insert_after(path, anchor, text):
    key = text.strip().split("\n")[0].split("=")[0].strip()
    if key in open(path).read():
        print("already present upstream, not added:", key)
        return
    edit(path, [(anchor, anchor + text)])


PD = "src/vk/mali_physical_device.c"
EXT = (
    PD,
    "static const struct vk_device_extension_table mali_device_extensions = {\n"
    "   .KHR_driver_properties = true,\n",
)
FEAT = (PD, "   *f = (struct vk_features) {\n")
PROP = (PD, "   *props = (struct vk_properties) {\n")


def core():
    """Build label (shown in driverInfo and checked after the build). Must succeed."""
    text = open("meson.build").read()
    new, n = re.subn(
        r"(?m)^(\s*)version : '[^']*',",
        r"\1version : 'ani-driver-build%s'," % BUILD,
        text,
        count=1,
    )
    if n != 1:
        sys.exit("PATCH FAILED in meson.build: could not find the project version line")
    open("meson.build", "w").write(new)
    print("patched: meson.build (label ani-driver-build%s)" % BUILD)


def map_fix():
    """Tolerates nested vkMapMemory calls (Vital for PS2, Vita, Switch, and Dolphin memory caching)."""
    if "map_count" in open("src/vk/mali_memory.h").read():
        print("already present upstream, not added: map_count")
        return
    edit(
        "src/vk/mali_memory.h",
        [
            (
                L("   void *map;", "", "   /* The range [lo, hi) ever mapped"),
                L(
                    "   void *map;",
                    "   /* vkMapMemory calls not yet matched by vkUnmapMemory (ani-driver). */",
                    "   uint32_t map_count;",
                    "",
                    "   /* The range [lo, hi) ever mapped",
                ),
            )
        ],
    )
    edit(
        "src/vk/mali_device_memory.c",
        [
            (
                L(
                    "   if (mem->map)",
                    '      return vk_errorf(dev, VK_ERROR_MEMORY_MAP_FAILED, "memory is already mapped");',
                ),
                "   /* ani-driver: a nested vkMapMemory is tolerated (see map_count). */",
            ),
            (
                L("      dmabuf_sync(mem, DMA_BUF_SYNC_START);", "      base = mem->dmabuf_map;"),
                L(
                    "      if (!mem->map)",
                    "         dmabuf_sync(mem, DMA_BUF_SYNC_START);",
                    "      base = mem->dmabuf_map;",
                ),
            ),
            (
                L("   mem->map = base + offset;", "   if (mem->mapped_hi == mem->mapped_lo) {"),
                L(
                    "   if (!mem->map)",
                    "      mem->map = base + offset;",
                    "   mem->map_count++;",
                    "   if (mem->mapped_hi == mem->mapped_lo) {",
                ),
            ),
            (
                L("   *ppData = mem->map;", "   return VK_SUCCESS;"),
                L("   *ppData = base + offset;", "   return VK_SUCCESS;"),
            ),
            (
                L("   if (!mem || !mem->map)", "      return VK_SUCCESS;", "   /* No cache maintenance."),
                L(
                    "   if (!mem || !mem->map)",
                    "      return VK_SUCCESS;",
                    "   if (mem->map_count > 1) {",
                    "      mem->map_count--;",
                    "      return VK_SUCCESS;",
                    "   }",
                    "   mem->map_count = 0;",
                    "   /* No cache maintenance.",
                ),
            ),
        ],
    )


HEADER = r'''/* ani-driver: console emulator diagnostics output.
 * Routes to stderr, logcat under "ani-driver", and optional file /sdcard/Download/ani-driver.log */
#ifndef MALI_PC_TRACE_H
#define MALI_PC_TRACE_H

#include <stdarg.h>
#include <stdio.h>
#include <unistd.h>
#ifdef __ANDROID__
#include <android/log.h>
#endif

static inline void
mali_pc_filelog(const char *line)
{
   fprintf(stderr, "[ani-driver] %s\n", line);
#ifndef ANI_QUIET_FILELOG
#ifdef __ANDROID__
   __android_log_write(ANDROID_LOG_INFO, "ani-driver", line);
#endif
   static const char *const paths[] = {
      "/sdcard/Download/ani-driver.log",
      "/storage/emulated/0/Download/ani-driver.log",
      "/data/local/tmp/ani-driver.log",
   };
   for (unsigned i = 0; i < sizeof(paths) / sizeof(paths[0]); i++) {
      if (access(paths[i], W_OK) != 0)
         continue;
      FILE *f = fopen(paths[i], "a");
      if (!f)
         continue;
      fprintf(f, "[pid %d] %s\n", (int)getpid(), line);
      fclose(f);
      return;
   }
#endif
}

#endif
'''

WAIST = r'''#include "mali_pc_trace.h"

static void __attribute__((format(printf, 1, 2)))
mali_pc_loge(const char *fmt, ...)
{
   char buf[512];
   va_list ap;
   va_start(ap, fmt);
   vsnprintf(buf, sizeof(buf), fmt, ap);
   va_end(ap);
   mesa_loge("%s", buf);
   mali_pc_filelog(buf);
}

static void __attribute__((constructor))
mali_pc_loaded(void)
{
   char cmd[256] = "?";
   FILE *f = fopen("/proc/self/cmdline", "r");
   if (f) {
      size_t n = fread(cmd, 1, sizeof(cmd) - 1, f);
      cmd[n] = 0;
      fclose(f);
   }
   char line[400];
   snprintf(line, sizeof(line), "ani-driver build BUILDNO loaded in %s", cmd);
   mali_pc_filelog(line);
}
'''


def log_output():
    """Diagnostics go to emulator stderr, logcat, and optional file."""
    path = "src/vk/mali_waist.c"
    s = open(path).read()
    marker = '#include "util/u_atomic.h"\n'
    if s.count(marker) != 1:
        fail(path, "include marker not found")
    head, tail = s.split(marker, 1)
    if tail.count("mesa_loge(") != 4:
        fail(path, "expected 4 mesa_loge calls, found %d" % tail.count("mesa_loge("))
    remember(path)
    open("src/vk/mali_pc_trace.h", "w").write(HEADER)
    open(path, "w").write(
        head
        + marker
        + WAIST.replace("BUILDNO", BUILD)
        + tail.replace("mesa_loge(", "mali_pc_loge(")
    )
    print("patched:", path)


def quiet_filelog():
    """Benchmark mode: strips per-message file I/O and logcat spam from mali_pc_trace.h."""
    edit(
        "src/vk/mali_pc_trace.h",
        [
            (
                "#ifndef MALI_PC_TRACE_H\n#define MALI_PC_TRACE_H\n",
                "#ifndef MALI_PC_TRACE_H\n#define MALI_PC_TRACE_H\n#define ANI_QUIET_FILELOG 1\n",
            )
        ],
    )


def diag():
    """Log render target AFBC compression stats (helpful for Switch/Vita/PS2 profiling)."""
    remember("src/vk/mali_image.c")
    if subprocess.call([sys.executable, "ani_diag.py"]) != 0:
        fail("src/vk/mali_image.c", "ani_diag.py did not match this upstream version")


def float_controls():
    """Half-float denormal controls (Required to prevent graphical corruption in PS2, Vita, Switch, and 3DS)."""
    insert_after(*EXT, "   .KHR_shader_float_controls = true,\n")
    insert_after(
        *PROP,
        L(
            "      .denormBehaviorIndependence = VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE,",
            "      .roundingModeIndependence = VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE,",
            "      .shaderDenormPreserveFloat16 = true,",
            "      .shaderDenormPreserveFloat32 = true,",
            "",
        ),
    )


def mirror_clamp():
    """Sampler mirror clamp extension (Used widely across 3DS, Wii U, and Dolphin texture scaling)."""
    insert_after(*EXT, "   .KHR_sampler_mirror_clamp_to_edge = true,\n")


def pipeline_cache_control():
    """Pipeline creation cache control (Speeds up shader compilation across Switch, Wii U, and Dolphin)."""
    insert_after(*EXT, "   .EXT_pipeline_creation_cache_control = true,\n")
    insert_after(*FEAT, "      .pipelineCreationCacheControl = true,\n")


def exp_load_store_op_none():
    """EXPERIMENTAL: Advertise VK_EXT_load_store_op_none to test tile load/store bandwidth impact."""
    insert_after(*EXT, "   .EXT_load_store_op_none = true,\n")


def exp_copy_commands2():
    """EXPERIMENTAL: Advertise VK_KHR_copy_commands2 to test copy/blit command overhead."""
    insert_after(*EXT, "   .KHR_copy_commands2 = true,\n")


FEATURES = {
    f.__name__: f
    for f in (
        map_fix,
        log_output,
        quiet_filelog,
        diag,
        float_controls,
        mirror_clamp,
        pipeline_cache_control,
        exp_load_store_op_none,
        exp_copy_commands2,
    )
}
DEPENDS = {
    "diag": ["log_output"],
    "quiet_filelog": ["log_output"],
}
APPLIED, SKIPPED = [], []


def apply(names):
    global _journal
    core()
    strict = os.environ.get("STRICT_FEATURES", "0") == "1"

    def run(n):
        global _journal
        if n in APPLIED or any(n == s[0] for s in SKIPPED):
            return n in APPLIED
        if n not in FEATURES:
            sys.exit("Unknown feature '%s'. Known: %s" % (n, ", ".join(FEATURES)))
        for d in DEPENDS.get(n, []):
            if not run(d):
                SKIPPED.append((n, "needs %s, which was skipped" % d))
                print("SKIPPED feature %s: needs %s" % (n, d))
                return False
        print("feature:", n)
        _journal = {}
        try:
            FEATURES[n]()
        except PatchMismatch as e:
            for p, text in _journal.items():
                open(p, "w").write(text)
            SKIPPED.append((n, str(e)))
            print("SKIPPED feature %s (rolled back safely): %s" % (n, e))
            if strict:
                sys.exit("STRICT_FEATURES is on: stopping because %s was skipped" % n)
            return False
        finally:
            _journal = None
        APPLIED.append(n)
        return True

    for n in names:
        run(n)
    with open("ani_features_report.txt", "w") as f:
        for n in APPLIED:
            f.write("applied: %s\n" % n)
        for n, why in SKIPPED:
            f.write("skipped: %s (%s)\n" % (n, why))
    
