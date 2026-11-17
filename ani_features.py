"""Feature patches for ani-driver (malisx2). Called by ani_ci.py.
Each feature is a function. To add one: write a function, add it to FEATURES, then put
its name in the FEATURES list of the workflow. Every edit is an exact-text match; if the
upstream text changes, the build stops with a clear message."""
import os, subprocess, sys

BUILD = os.environ.get("BUILD_NO", "0")
L = lambda *a: "\n".join(a)
_done = set()


def fail(path, what):
    sys.exit("PATCH FAILED in %s: %s" % (path, what))


def edit(path, pairs):
    s = open(path).read()
    for old, new in pairs:
        if s.count(old) != 1:
            fail(path, "expected exactly one match of:\n" + old)
        s = s.replace(old, new, 1)
    open(path, "w").write(s)
    print("patched:", path)


def insert_after(path, anchor, text):
    edit(path, [(anchor, anchor + text)])


PD = "src/vk/mali_physical_device.c"
EXT = (PD, "static const struct vk_device_extension_table mali_device_extensions = {\n"
           "   .KHR_driver_properties = true,\n")
FEAT = (PD, "   *f = (struct vk_features) {\n")
PROP = (PD, "   *props = (struct vk_properties) {\n")


def core():
    edit("meson.build", [("version : '0.5.0',", "version : 'ani-driver-build%s'," % BUILD)])


def map_fix():
    """A second vkMapMemory on already mapped memory returns the same view."""
    edit("src/vk/mali_memory.h", [(
        L("   void *map;", "", "   /* The range [lo, hi) ever mapped"),
        L("   void *map;", "   /* vkMapMemory calls not yet matched by vkUnmapMemory (ani-driver). */",
          "   uint32_t map_count;", "", "   /* The range [lo, hi) ever mapped"))])
    edit("src/vk/mali_device_memory.c", [
        (L("   if (mem->map)",
           '      return vk_errorf(dev, VK_ERROR_MEMORY_MAP_FAILED, "memory is already mapped");'),
         "   /* ani-driver: a nested vkMapMemory is tolerated (see map_count). */"),
        (L("      dmabuf_sync(mem, DMA_BUF_SYNC_START);", "      base = mem->dmabuf_map;"),
         L("      if (!mem->map)", "         dmabuf_sync(mem, DMA_BUF_SYNC_START);",
           "      base = mem->dmabuf_map;")),
        (L("   mem->map = base + offset;", "   if (mem->mapped_hi == mem->mapped_lo) {"),
         L("   if (!mem->map)", "      mem->map = base + offset;", "   mem->map_count++;",
           "   if (mem->mapped_hi == mem->mapped_lo) {")),
        (L("   *ppData = mem->map;", "   return VK_SUCCESS;"),
         L("   *ppData = base + offset;", "   return VK_SUCCESS;")),
        (L("   if (!mem || !mem->map)", "      return VK_SUCCESS;", "   /* No cache maintenance."),
         L("   if (!mem || !mem->map)", "      return VK_SUCCESS;", "   if (mem->map_count > 1) {",
           "      mem->map_count--;", "      return VK_SUCCESS;", "   }", "   mem->map_count = 0;",
           "   /* No cache maintenance.")),
    ])


HEADER = r'''/* ani-driver: diagnostics output. Header only.
 * Every line goes to stderr (Vita3K+ keeps it in panvk_log.txt), to logcat under the
 * tag "ani-driver" and, if /sdcard/Download/ani-driver.log already exists, to that
 * file too. */
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
    """Diagnostics go to stderr, logcat and an optional file."""
    open("src/vk/mali_pc_trace.h", "w").write(HEADER)
    path = "src/vk/mali_waist.c"
    s = open(path).read()
    marker = '#include "util/u_atomic.h"\n'
    if s.count(marker) != 1:
        fail(path, "include marker not found")
    head, tail = s.split(marker, 1)
    if tail.count("mesa_loge(") != 4:
        fail(path, "expected 4 mesa_loge calls, found %d" % tail.count("mesa_loge("))
    open(path, "w").write(head + marker + WAIST.replace("BUILDNO", BUILD) +
                          tail.replace("mesa_loge(", "mali_pc_loge("))
    print("patched:", path)


def diag():
    """Log which render targets get compression (needs log_output)."""
    subprocess.check_call([sys.executable, "ani_diag.py"])


def float_controls():
    """Vita3K stops emulating half-float denormals when float controls are present."""
    insert_after(*EXT, "   .KHR_shader_float_controls = true,\n")
    insert_after(*PROP, L(
        "      .denormBehaviorIndependence = VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE,",
        "      .roundingModeIndependence = VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE,",
        "      .shaderDenormPreserveFloat16 = true,",
        "      .shaderDenormPreserveFloat32 = true,", ""))


def mirror_clamp():
    """mali_sampler.c already handles mirror-clamp."""
    insert_after(*EXT, "   .KHR_sampler_mirror_clamp_to_edge = true,\n")


def pipeline_cache_control():
    """mali_pipeline.c already returns VK_PIPELINE_COMPILE_REQUIRED for the flag."""
    insert_after(*EXT, "   .EXT_pipeline_creation_cache_control = true,\n")
    insert_after(*FEAT, "      .pipelineCreationCacheControl = true,\n")


FEATURES = {f.__name__: f for f in
            (map_fix, log_output, diag, float_controls, mirror_clamp, pipeline_cache_control)}
DEPENDS = {"diag": ["log_output"]}


def apply(names):
    core()

    def run(n):
        if n in _done:
            return
        if n not in FEATURES:
            sys.exit("Unknown feature '%s'. Known: %s" % (n, ", ".join(FEATURES)))
        for d in DEPENDS.get(n, []):
            run(d)
        print("feature:", n)
        FEATURES[n]()
        _done.add(n)

    for n in names:
        run(n)
