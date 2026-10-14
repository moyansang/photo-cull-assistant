# Decoder redesign research and verification (2026-10-13)

## Implementation decisions

- `image_decode.py` centralizes native-size standard-image reading, RAW embedded-preview extraction, selective full RAW decoding, orientation, color conversion, codec capability discovery, cache ownership and source evidence.
- `preview.py` makes a separate 1600px thumbnail without mutating the shared source. Failed paired JPG falls back to RAW. Existing v04/v05 workspace coordinate systems remain intact. Corrupt derived preview files are regenerated.
- `shared_decode.py` shares one lazily loaded source across preview, face/body checks and detail-cache warming. Same-photo nested scopes reuse the parent. RAW escalation preserves borrowed old images until their context exits. Failed escalation is remembered within the scope.
- `exif_utils.py` enables metadata parsing for the added formats and reads nested EXIF timestamp/subsecond tags. DateTimeOriginal no longer prematurely stops ExifRead before its subsecond tag.
- `pyproject.toml` requests Pillow >=11.3 (native AVIF); optional `codecs` installs the decoder-only `pi-heif==1.4.0`. `build_exe.ps1` collects that decoder and package license metadata in the existing one-directory build.

Normal paired JPG is decoded at native resolution. RAW-only/bad JPG uses an embedded JPEG/bitmap first. `RawPy.open_file()` reads the container; unlike `rawpy.imread()`, it does not eagerly unpack the sensor. Only an absent/bad thumbnail or insufficient evidence triggers full sensor unpack/postprocess. There is no half-size stage or group-score parameter. A viewer's explicit full-RAW command is independent of scoring.

Pixel evidence floors are provisional: 512px short image side without a requested subject region, or 96px minimum clipped subject-region side. Callers may explicitly request refinement for missing detail evidence. These thresholds are engineering safeguards, not an accuracy claim or quality cutoff. Pixel coverage after upgrading is not proof of sharpness. Unsupported full RAW leaves usable preview evidence marked insufficient for review.

## Official reusable dependencies and licenses

No external implementation was copied. Only documented APIs are used.

| Module / usage | Official source | License / decision |
| --- | --- | --- |
| Standard decode, EXIF orientation, BMP and native AVIF | [Pillow](https://github.com/python-pillow/Pillow), [AVIF plugin](https://github.com/python-pillow/Pillow/blob/main/src/PIL/AvifImagePlugin.py), [license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) | MIT-CMU. Use the existing dependency; preserve distribution notices. |
| ICC conversion in `image_decode.py` | [Pillow ImageCms](https://github.com/python-pillow/Pillow/blob/main/src/PIL/ImageCms.py) | Via Pillow/LittleCMS. Convert valid ICC profiles to sRGB; disclose failed profiles. Untagged ordinary RGB is assumed sRGB. Unmanaged wide-gamut/HDR NCLX is flagged unverified. |
| RAW container/thumbnail/full decode | [rawpy](https://github.com/letmaik/rawpy), [RawPy API](https://letmaik.github.io/rawpy/api/rawpy.RawPy.html), [imread implementation](https://github.com/letmaik/rawpy/blob/main/rawpy/__init__.py) | rawpy MIT; bundled LibRaw LGPL-2.1 / its applicable notices. Existing dependency, no GPL demosaic packs introduced. Preserve binary/source notices. |
| HEIC/HEIF decode | [pillow-heif upstream](https://github.com/bigcat88/pillow_heif), [pi-heif 1.4.0 release](https://pypi.org/project/pi-heif/1.4.0/) | pi-heif bindings BSD-3-Clause; decoder wheels LGPLv3 (libheif/libde265). Decoder-only package avoids x265. |
| Rejected HEIF default | [pillow-heif bundled licenses](https://github.com/bigcat88/pillow_heif/blob/master/LICENSES_bundled.txt) | Normal pillow-heif wheels include GPLv2 x265. Not added to this PolyForm Noncommercial application's dependency/build. |
| Considered AVIF alternative | [pillow-avif-plugin](https://github.com/fdintino/pillow-avif-plugin) | Not added: native Pillow AVIF avoids duplicate plugin registration and dependency packaging. |
| Capture time fallback | [ExifRead](https://github.com/ianare/exif-py), [license](https://github.com/ianare/exif-py/blob/master/LICENSE) | BSD-3-Clause. Existing optional RAW dependency. |
| `shared_decode.py` ownership and LRU | Python `contextlib`, `threading`, `collections` | New application code using Python standard-library APIs. |

### Distribution and maintenance gates

pi-heif 1.4.0 is the upstream's final decoder-only release. A maintained replacement or audited decoder-only build must be reviewed when security updates are needed. Do not silently switch to GPL-encoder wheels.

The installed Linux wheel reports libheif 1.23.0 and libde265 1.1.0. Its bundled notice still points to older source tags (1.18.1 / 1.0.15). That notice is not sufficient evidence that distribution obligations for the actual shipped binaries are complete. Before any binary release, verify the exact Windows wheel's libraries, retain applicable full license texts and corresponding source materials/offers, and ensure LGPL replacement/relinking rights. The one-directory format and metadata collection help; they are not a completed legal compliance review. This work neither publishes nor claims release readiness.

## Bounds, reuse and errors

- One native decoder call at a time; up to two active scan image scopes.
- 128MiB LRU retained RGB cache, conservatively counted at >=4 bytes/pixel; source path/size/mtime and policy version key it.
- 100MP pre-decode image-size protection. Larger images raise a clear resource error, never a poor-quality verdict.
- Caller-owned copies are independent of cache eviction. Native decoder peak working memory is additional and cannot be strictly capped by the RGB cache. Viewer/consumer caches must also remain bounded.
- Source kinds: `paired_jpeg`, `standard_image`, `raw_embedded_jpeg`, `raw_embedded_bitmap`, `raw_full_decode`. `image.info['decode_evidence']` also contains source dimensions, warnings, resolution and pixel-evidence sufficiency.
- `codec_capabilities()` checks installed HEVC/AV1 decoder support, not just filename extensions. RAW capability remains per-camera/per-compression; errors report unsupported/corrupt/unavailable rather than assigning a bad-quality score.
- RAW output is camera-white-balanced 8-bit sRGB with no automatic brightness boost. LibRaw applies full-output orientation once. Embedded EXIF takes priority over the LibRaw flip code. Transparency is composited on white. 16-bit grayscale TIFF/HEIF display conversion avoids clipping all values above 255.

## Validation and provenance

Focused command (real fixtures optional, downloaded separately; tests never download):

```sh
PHOTO_CULL_CODEC_FIXTURES=/tmp/photo-cull-codec-fixtures PYTHONPATH=src \
  /workspace/scratch/d4e33c625c08/cloud_qa_homepage/.venv/bin/python -m pytest \
  tests/test_image_decode_policy.py tests/test_image_codec_integration.py \
  tests/test_preview_orientation.py tests/test_shared_decode.py \
  tests/test_focus_image_cache.py -q -p no:cacheprovider
```

Result after implementation: **61 passed**. Coverage includes preferred full JPG, bad JPG fallback, missing/small RAW preview, region-driven escalation, no score-driven escalation API, explicit full RAW viewing, cache invalidation/eviction/concurrency, true rawpy missing-thumbnail exceptions, unpack avoidance, single-flight reuse, nested ownership, orientation, ICC, alpha, 16-bit tone conversion and EXIF subsecond parsing.

Actual native codec checks:

- Native AVIF encode/decode round-trip generated in an isolated test directory.
- Six upstream HEIF samples: 8/10/12-bit RGB, 8/12-bit RGBA and 12-bit grayscale.
- Real portrait HEIC (3024×4032), metadata HEIC, and two corrupt/empty HEIC failures.
- Canon 40D sRAW CR2: embedded 1936×1288, full 1944×1296.
- Nikon NEF upstream `iss030e122639`: embedded 4256×2832, full 4284×2844.

Exact sizes, SHA256 hashes, upstream URLs, package versions and fixture-license limitations are in [decode-validation-20261013.json](decode-validation-20261013.json). Upstream fixture images and archives are temporary test inputs outside the repository and are **not redistributed**. CR2's upstream attribution is rawsamples.ch, CC BY-NC-SA 4.0; other individual fixture-photo redistribution permissions were not established.

Remaining unverified: real RW2/ARW/CR3/ORF/RAF/DNG camera/compression variants; all orientations across each camera; HDR photometric accuracy; Windows native codec packaging; exhaustive memory-pressure behavior; clinical/statistical sharpness accuracy. Eight RAW-extension protocol tests are mocks and do not establish this missing coverage. PEF/SRW were not added.

Environment change: installation output reports only `pi-heif==1.4.0` added and existing Pillow 12.3.0 reused; no package upgrade was reported. A complete pre-install freeze was not captured. Frozen benchmark source directories and user originals were not edited.
