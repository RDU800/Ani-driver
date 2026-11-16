"""ani-driver patches for malisx2. Run from the repo root: python3 ani_patches.py
Every edit is an exact-text replacement; if the upstream text changes, the build stops
with a clear message instead of silently building something different."""
import os, sys

BUILD = os.environ["BUILD_NO"]


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


L = lambda *a: "\n".join(a)

# 1. build label (shown in driverInfo and checked after the build)
edit("meson.build", [("version : '0.5.0',", "version : 'ani-driver-build%s'," % BUILD)])

# 2. a second vkMapMemory on already mapped memory returns the same view
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

# 3. diagnostics output: stderr (Vita3K+ keeps it in panvk_log.txt), logcat, optional file
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
open("src/vk/mali_pc_trace.h", "w").write(HEADER)

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
'''.replace("BUILDNO", BUILD)

path = "src/vk/mali_waist.c"
s = open(path).read()
marker = '#include "util/u_atomic.h"\n'
if s.count(marker) != 1:
    fail(path, "include marker not found")
head, tail = s.split(marker, 1)
if tail.count("mesa_loge(") != 4:
    fail(path, "expected 4 mesa_loge calls, found %d" % tail.count("mesa_loge("))
open(path, "w").write(head + marker + WAIST + tail.replace("mesa_loge(", "mali_pc_loge("))
print("patched:", path)

# 4. extensions the system driver has and Vita3K uses
edit("src/vk/mali_physical_device.c", [
    ("static const struct vk_device_extension_table mali_device_extensions = {\n"
     "   .KHR_driver_properties = true,\n",
     "static const struct vk_device_extension_table mali_device_extensions = {\n"
     "   .KHR_driver_properties = true,\n"
     "   /* ani-driver: mirror-clamp is handled in mali_sampler.c. The compiler keeps\n"
     "    * denormals by default (ftz_mode in mali_shader.c), so float controls can say\n"
     "    * so; Vita3K then stops emulating half-float denormals. mali_pipeline.c already\n"
     "    * returns VK_PIPELINE_COMPILE_REQUIRED for the fail-on-compile flag. */\n"
     "   .KHR_sampler_mirror_clamp_to_edge = true,\n"
     "   .KHR_shader_float_controls = true,\n"
     "   .EXT_pipeline_creation_cache_control = true,\n"),
    ("   *f = (struct vk_features) {\n",
     "   *f = (struct vk_features) {\n      .pipelineCreationCacheControl = true,\n"),
    ("   *props = (struct vk_properties) {\n",
     "   *props = (struct vk_properties) {\n"
     "      .denormBehaviorIndependence = VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE,\n"
     "      .roundingModeIndependence = VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE,\n"
     "      .shaderDenormPreserveFloat16 = true,\n"
     "      .shaderDenormPreserveFloat32 = true,\n"),
])
