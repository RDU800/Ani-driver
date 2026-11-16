"""ani-driver render-target diagnostics. Run from the repo root after ani_patches.py.
Logs, for every colour or depth target, whether it got AFBC compression and the first
rule that refused it. It does not change which modifier is chosen."""
import sys

path = "src/vk/mali_image.c"
s = open(path).read()

old = "static uint64_t\nchoose_modifier(const struct mali_image *image,"
new = "static uint64_t\nchoose_modifier_impl(const struct mali_image *image,"
anchor = "   return DRM_FORMAT_MOD_ARM_16X16_BLOCK_U_INTERLEAVED;\n}\n"
if s.count(old) != 1 or s.count(anchor) != 1:
    sys.exit("PATCH FAILED in %s: choose_modifier anchors not found" % path)
s = s.replace(old, new, 1)

DIAG = r"""
/* ani-driver diagnostics (lines start with "ani-driver"). */
#include <pthread.h>

static const char *
pc_afbc_reason(const struct mali_image *image, const enum pipe_format *formats,
               unsigned arch, const VkImageCreateInfo *info)
{
   const VkImageUsageFlags usage = image->vk.usage | image->vk.stencil_usage;
   const VkImageCreateFlags fl = image->vk.create_flags;
   if (arch != 7)
      return "not-v7";
   if (!(usage & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT))
      return vk_format_is_depth_or_stencil(image->vk.format) ? "v7-depth-never" : "no-colour-usage";
   if (usage & VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT)
      return "usage-depth-stencil";
   if (usage & VK_IMAGE_USAGE_STORAGE_BIT)
      return "usage-storage";
   if (usage & VK_IMAGE_USAGE_HOST_TRANSFER_BIT)
      return "usage-host-transfer";
   if (image->plane_count != 1)
      return "multi-plane";
   if (vk_format_is_depth_or_stencil(image->vk.format))
      return "depth-format";
   if (!v7_afbc_format(formats[0]))
      return "format-not-8bit-rgba";
   if (fl & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT)
      return "flag-mutable-format";
   if (fl & VK_IMAGE_CREATE_BLOCK_TEXEL_VIEW_COMPATIBLE_BIT)
      return "flag-block-texel-view";
   if (fl & (VK_IMAGE_CREATE_ALIAS_BIT | VK_IMAGE_CREATE_DISJOINT_BIT))
      return "flag-alias-or-disjoint";
   if (fl & (VK_IMAGE_CREATE_SPARSE_BINDING_BIT | VK_IMAGE_CREATE_SPARSE_RESIDENCY_BIT |
             VK_IMAGE_CREATE_SPARSE_ALIASED_BIT))
      return "flag-sparse";
   if (image->vk.tiling != VK_IMAGE_TILING_OPTIMAL)
      return "tiling-not-optimal";
   if (image->vk.image_type != VK_IMAGE_TYPE_2D)
      return "not-2d";
   if (image->vk.samples != 1)
      return "multisampled";
   if (image->vk.mip_levels != 1)
      return "mip-levels";
   if (image->vk.array_layers != 1)
      return "array-layers";
   if (external_memory(info))
      return "external-or-swapchain";
   const unsigned min = mali_afbc_min_extent(formats[0]);
   if (image->vk.extent.width < min || image->vk.extent.height < min)
      return "too-small";
   return "other";
}

struct pc_img_key {
   int fmt;
   uint32_t usage, flags, samples;
   uint64_t mod;
   char why[32];
   uint32_t count;
};

static struct pc_img_key pc_keys[128];
static unsigned pc_nkeys;
static unsigned pc_total;
static uint64_t pc_px[3]; /* colour compressed, colour plain, depth/stencil */
static pthread_mutex_t pc_lock = PTHREAD_MUTEX_INITIALIZER;

static void
pc_log_image(const struct mali_image *image, const enum pipe_format *formats,
             unsigned arch, const VkImageCreateInfo *info, uint64_t mod)
{
   const VkImageUsageFlags usage = image->vk.usage | image->vk.stencil_usage;
   if (!(usage & (VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
                  VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT)))
      return;
   const bool comp = mod == MALI_MOD_AFBC || mod == MALI_MOD_AFBC_YTR;
   const bool ds = vk_format_is_depth_or_stencil(image->vk.format);
   const char *why = comp ? "compressed" : pc_afbc_reason(image, formats, arch, info);
   const uint64_t px = (uint64_t)image->vk.extent.width * image->vk.extent.height *
                       image->vk.array_layers * image->vk.samples;
   char buf[400];
   bool print = false;
   pthread_mutex_lock(&pc_lock);
   pc_px[ds ? 2 : (comp ? 0 : 1)] += px;
   pc_total++;
   unsigned k;
   for (k = 0; k < pc_nkeys; k++) {
      const struct pc_img_key *e = &pc_keys[k];
      if (e->fmt == (int)image->vk.format && e->usage == (uint32_t)usage &&
          e->flags == (uint32_t)image->vk.create_flags &&
          e->samples == (uint32_t)image->vk.samples && e->mod == mod &&
          strcmp(e->why, why) == 0)
         break;
   }
   if (k == pc_nkeys && pc_nkeys < 128) {
      struct pc_img_key *e = &pc_keys[pc_nkeys++];
      e->fmt = (int)image->vk.format;
      e->usage = (uint32_t)usage;
      e->flags = (uint32_t)image->vk.create_flags;
      e->samples = (uint32_t)image->vk.samples;
      e->mod = mod;
      snprintf(e->why, sizeof(e->why), "%s", why);
      e->count = 0;
   }
   if (k < pc_nkeys) {
      pc_keys[k].count++;
      if (pc_keys[k].count == 1 || pc_keys[k].count == 100 || pc_keys[k].count == 1000)
         print = true;
   }
   if (print) {
      snprintf(buf, sizeof(buf),
               "ani-driver target: vkfmt=%d pipe=%s %ux%u layers=%u samples=%u usage=0x%x "
               "flags=0x%x tiling=%d -> %s (%s) [seen %u]",
               (int)image->vk.format, util_format_short_name(formats[0]),
               image->vk.extent.width, image->vk.extent.height, image->vk.array_layers,
               (unsigned)image->vk.samples, (unsigned)usage, (unsigned)image->vk.create_flags,
               (int)image->vk.tiling, comp ? "AFBC" : "plain", why,
               k < pc_nkeys ? pc_keys[k].count : 1u);
      mesa_logi("%s", buf);
      mali_pc_filelog(buf);
   }
   if (pc_total % 50 == 0) {
      snprintf(buf, sizeof(buf),
               "ani-driver summary after %u targets: colour AFBC %llu kpx, colour plain %llu kpx, "
               "depth/stencil %llu kpx",
               pc_total, (unsigned long long)(pc_px[0] / 1000),
               (unsigned long long)(pc_px[1] / 1000), (unsigned long long)(pc_px[2] / 1000));
      mesa_logi("%s", buf);
      mali_pc_filelog(buf);
   }
   pthread_mutex_unlock(&pc_lock);
}

static uint64_t
choose_modifier(const struct mali_image *image, const enum pipe_format *formats,
                unsigned arch, const VkImageCreateInfo *info)
{
   const uint64_t mod = choose_modifier_impl(image, formats, arch, info);
   pc_log_image(image, formats, arch, info, mod);
   return mod;
}
"""
s = s.replace(anchor, anchor + DIAG, 1)
k = s.find('#include "')
s = s[:k] + '#include "mali_pc_trace.h"\n' + s[k:]
open(path, "w").write(s)
print("patched:", path)
