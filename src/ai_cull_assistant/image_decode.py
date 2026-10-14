"""Evidence-driven image decoding shared by scanning and viewing.

Returned images belong to callers. Decoder/cache limits bound retained RGB data
and simultaneous native decoder calls, not LibRaw's internal peak allocations.
No quality score or group rank is accepted as a RAW escalation trigger.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
import importlib
import hashlib
import os
import sys
from functools import lru_cache
import math
from pathlib import Path
from threading import BoundedSemaphore, RLock

from PIL import Image, ImageOps

from .models import RAW_EXTENSIONS
from .scan_diagnostics import note, operation

try:
    import rawpy
except (ImportError, OSError):  # Optional native dependency may lack its DLLs.
    rawpy = None

DECODE_VERSION = "source-first-v1"
CACHE_BYTES = 128 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
MIN_PREVIEW_SIDE = 512  # Provisional evidence floor, not a quality threshold.
_decode_slots = BoundedSemaphore(1)
_cache_lock = RLock()
_codec_lock = RLock()
_cache: OrderedDict[tuple, Image.Image] = OrderedDict()
_cache_bytes = 0
_cache_generation = 0
_codecs_ready = False
_codec_status: dict[str, dict] = {}


class DecodeError(OSError):
    """Unavailable/unsupported/corrupt source; never a bad-quality verdict."""


class CodecUnavailable(DecodeError):
    pass


def register_image_codecs() -> None:
    """Register available decoders once, without installing anything at runtime."""
    global _codecs_ready
    with _codec_lock:
        if _codecs_ready:
            return
        Image.init()
        heif_detail = "HEIC/HEIF 解码器不可用；请安装 codecs 可选依赖"
        heif_decoder = None
        # Decode-only wheels avoid the GPL x265 encoder in pillow-heif wheels.
        try:
            module = importlib.import_module("pi_heif")
            module.register_heif_opener(decode_threads=1, thumbnails=False)
            heif_info = module.libheif_info()
            if any("hevc" in str(value).lower() or "de265" in str(key).lower()
                   for key, value in heif_info.get("decoders", {}).items()):
                heif_decoder = "pi-heif"
            else:
                heif_detail = "HEIC/HEIF 模块已安装，但缺少 HEVC 解码器"
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            heif_detail += f" ({type(exc).__name__})"
        for suffix in (".heic", ".heif"):
            _codec_status[suffix] = dict(available=heif_decoder is not None,
                decoder=heif_decoder, detail=heif_detail if heif_decoder is None else "HEIF decoder loaded; individual files still require validation")
        avif_available = False
        try:
            avif = importlib.import_module("PIL.AvifImagePlugin")
            avif_available = bool(avif.SUPPORTED and any(
                avif._avif.decoder_codec_available(entry.split()[0])
                for entry in avif._avif.codec_versions().split(", ") if entry))
            avif.DEFAULT_MAX_THREADS = 1
        except (ImportError, OSError, AttributeError):
            pass
        _codec_status[".avif"] = dict(available=avif_available,
            decoder="Pillow/libavif" if avif_available else None,
            detail="AVIF decoder loaded" if avif_available else "AVIF 解码器不可用；需要带 AVIF 支持的 Pillow 11.3 或更新版本")
        _codecs_ready = True


def codec_capabilities() -> dict[str, dict]:
    register_image_codecs()
    result = deepcopy(_codec_status)
    for suffix in (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"):
        fmt = Image.registered_extensions().get(suffix)
        result[suffix] = dict(available=fmt in Image.OPEN, decoder="Pillow",
                             detail="Registered decoder; individual files still require validation")
    for suffix in RAW_EXTENSIONS:
        result[suffix] = dict(available=rawpy is not None,
            decoder="rawpy/LibRaw" if rawpy is not None else None,
            detail=(f"LibRaw {getattr(rawpy, 'libraw_version', 'unknown')}; camera/compression support must be validated per file"
                    if rawpy is not None else "RAW 解码器不可用；请安装 raw 可选依赖"))
    return result


def _check_dimensions(size) -> None:
    width, height = size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise DecodeError(f"图像尺寸超过解码内存保护范围：{width}×{height}")


def _image_bytes(image) -> int:
    # Pillow commonly stores RGB as four bytes per pixel internally.
    return image.width * image.height * max(4, len(image.getbands()))


def clear_decode_cache() -> None:
    global _cache_bytes, _cache_generation
    with _cache_lock:
        # An already-running decode may still return its caller-owned image,
        # but must not repopulate a cache cleared while that work was in flight.
        _cache_generation += 1
        for image in _cache.values():
            image.close()
        _cache.clear()
        _cache_bytes = 0


def decode_cache_info() -> dict:
    with _cache_lock:
        return dict(entries=len(_cache), bytes=_cache_bytes, max_bytes=CACHE_BYTES,
                    max_concurrent_decoders=1, max_image_pixels=MAX_IMAGE_PIXELS)


def _owned_copy(image):
    owned = image.copy()
    try:
        owned.info = deepcopy(image.info)
    except BaseException:
        owned.close()
        raise
    return owned


def _stat_identity(stat):
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
            stat.st_dev, stat.st_ino)


def _windows_extended_path(path):
    """Use Unicode extended paths, including UNC, without double-prefixing."""
    import ntpath
    value = ntpath.normpath(str(path))
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


@lru_cache(maxsize=1)
def _windows_identity_api():
    # Lazily bind: importing the decoder must work on non-Windows platforms.
    import ctypes
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in
                    ('CreationTime', 'LastAccessTime', 'LastWriteTime', 'ChangeTime')]
        _fields_ += [('FileAttributes', ctypes.c_uint32)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    declarations = {
        'CreateFileW': ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                         ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                         wintypes.HANDLE], wintypes.HANDLE),
        'GetFileInformationByHandleEx': ([wintypes.HANDLE, ctypes.c_int,
                                         ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        'GetVolumeInformationByHandleW': ([wintypes.HANDLE, wintypes.LPWSTR,
            wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.LPWSTR, wintypes.DWORD], wintypes.BOOL),
        'GetFinalPathNameByHandleW': ([wintypes.HANDLE, wintypes.LPWSTR,
                                      wintypes.DWORD, wintypes.DWORD], wintypes.DWORD),
        'GetDriveTypeW': ([wintypes.LPCWSTR], wintypes.UINT),
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api, FileBasicInfo


def _windows_change_time(path):
    """Read a native change token only on the opened file's fixed NTFS volume.

    Unknown/remote/removable filesystems and failed queries use content hashing.
    ChangeTime detects normal completed rewrites, not deliberately suppressed
    timestamps. Never retain an open handle or request write/USN privileges.
    """
    import ctypes
    handle = None
    api = None
    try:
        api, basic_type = _windows_identity_api()
        handle = api.CreateFileW(_windows_extended_path(path), 0x80, 7,
                                 None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            handle = None
            return None
        filesystem = ctypes.create_unicode_buffer(261)
        if not api.GetVolumeInformationByHandleW(
                handle, None, 0, None, None, None, filesystem, len(filesystem)):
            return None
        if filesystem.value.upper() != 'NTFS':
            return None
        # A GUID path identifies the actual target volume, including junctions
        # and mount points; the original drive letter could name another disk.
        final_path = ctypes.create_unicode_buffer(32768)
        # FILE_NAME_OPENED avoids normalized-name traversal on an attributes-only
        # handle; VOLUME_NAME_GUID still identifies the actual target volume.
        length = api.GetFinalPathNameByHandleW(handle, final_path, len(final_path), 9)
        if not length or length >= len(final_path):
            return None
        value = final_path.value
        if not value.startswith('\\\\?\\Volume{') or '}\\' not in value:
            return None
        volume_root = value[:value.index('}\\') + 2]
        if api.GetDriveTypeW(volume_root) != 3:  # DRIVE_FIXED only
            return None
        basic = basic_type()
        if not api.GetFileInformationByHandleEx(handle, 0, ctypes.byref(basic),
                                                ctypes.sizeof(basic)):
            return None
        return int(basic.ChangeTime) if basic.ChangeTime > 0 else None
    except (OSError, AttributeError, ValueError):
        return None
    finally:
        if handle is not None:
            api.CloseHandle(handle)


def _content_identity(path, expected):
    """Fallback reads every byte on every call, with bounded memory."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        before = _stat_identity(os.fstat(stream.fileno()))
        # Python 3.12 Windows path.stat ctime may mean creation time while
        # fstat ctime means write time. Bind the handle with common fields;
        # compare full timestamps only within the same API before/after.
        if before[:2] + before[3:] != expected[:2] + expected[3:]:
            raise DecodeError('源文件在校验时发生变化；请重试')
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        if _stat_identity(os.fstat(stream.fileno())) != before:
            raise DecodeError('源文件在校验时发生变化；请重试')
    return ('sha256', digest.hexdigest())


def source_signature(path) -> tuple:
    path = Path(path).resolve()
    identity = _stat_identity(path.stat())
    signature = (str(path), *identity)
    if sys.platform != 'win32':
        return signature
    # Python's Windows ctime can be creation time, not a rewrite indicator.
    change_time = _windows_change_time(path)
    token = (('win-change-time', change_time) if change_time is not None
             else _content_identity(path, identity))
    if _stat_identity(path.stat()) != identity:
        raise DecodeError('源文件在校验时发生变化；请重试')
    return (*signature, token)


def asset_source_signature(asset) -> tuple:
    """Version all possible sources, including a missing/reappearing pair."""
    sources = []
    signatures = {}
    for role in ('primary_path', 'jpg_path', 'raw_path'):
        path = getattr(asset, role, None)
        if path is not None:
            path = Path(path)
            if path not in signatures:
                try:
                    signatures[path] = source_signature(path)
                except OSError as exc:
                    signatures[path] = (str(path.absolute()), None, type(exc).__name__)
            sources.append((role, signatures[path]))
    return tuple(sources)


def _cached_load(path, stage, loader):
    global _cache_bytes
    with _decode_slots:
        # Stat only after acquiring the slot: queued callers must not carry an
        # old key across a replacement while another decoder is running.
        try:
            signature = source_signature(path)
        except OSError as exc:
            raise DecodeError(f"无法读取源文件 {Path(path).name}：{type(exc).__name__}") from exc
        key = (DECODE_VERSION, stage, signature)
        with _cache_lock:
            generation = _cache_generation
            # A preview request may previously have needed full RAW. Reuse it.
            full_key = (DECODE_VERSION, ("raw_full", True), key[-1])
            if stage == ("raw_preview", True) and full_key in _cache:
                key = full_key
            if key in _cache:
                _cache.move_to_end(key)
                note("decode_cache", "hit")
                return _owned_copy(_cache[key])
        image = loader()
        retained = None
        try:
            size = _image_bytes(image)
            if image.info.get("decode_evidence", {}).get("source_kind") == "raw_full_decode":
                key = (DECODE_VERSION, ("raw_full", True), signature)
            try:
                unchanged = source_signature(path) == signature
            except OSError:
                unchanged = False
            with _cache_lock:
                # Never associate an in-flight snapshot with a replaced file.
                # Large images belong to the requesting scope only.
                if unchanged and generation == _cache_generation and size <= CACHE_BYTES:
                    # Allocate before evicting so a failed copy leaves other
                    # usable entries intact and still closes this new owner.
                    retained = _owned_copy(image)
                    replaced = _cache.pop(key, None)
                    if replaced is not None:
                        _cache_bytes -= _image_bytes(replaced)
                        replaced.close()
                    while _cache and _cache_bytes + size > CACHE_BYTES:
                        _, evicted = _cache.popitem(last=False)
                        _cache_bytes -= _image_bytes(evicted)
                        evicted.close()
                    _cache[key] = retained
                    retained = None
                    _cache_bytes += size
            return image
        except BaseException:
            image.close()
            raise
        finally:
            if retained is not None:
                retained.close()


def orient_raw_thumbnail(image, flip):
    """LibRaw flip is not EXIF. Thumbnail EXIF, when present, takes precedence."""
    if image.getexif().get(274) in range(1, 9):
        return ImageOps.exif_transpose(image)
    transform = {3: Image.Transpose.ROTATE_180, 5: Image.Transpose.ROTATE_90,
                 6: Image.Transpose.ROTATE_270}.get(flip)
    return image.transpose(transform) if transform is not None else image


def _normalize_rgb(image, warnings, *, orient=True):
    """Apply orientation and ICC conversion once, then flatten transparency."""
    _check_dimensions(image.size)
    source = ImageOps.exif_transpose(image) if orient else image.copy()
    profile = source.info.get("icc_profile")
    nclx = source.info.get("nclx_profile") or {}
    if nclx and not profile:
        primaries = nclx.get("color_primaries")
        transfer = nclx.get("transfer_characteristics")
        if primaries not in (None, 1) or transfer not in (None, 1, 13):
            # Do not label an unmanaged wide-gamut/HDR conversion as sRGB.
            warnings.append("nclx_color_unverified")
        if transfer in (16, 18):
            warnings.append("hdr_display_conversion_unverified")
    try:
        if profile:
            alpha = cms_source = converted = None
            conversion_errors = (ImportError, OSError, ValueError, TypeError)
            try:
                from PIL import ImageCms
                # Pillow wraps incompatible color transforms in PyCMSError,
                # which inherits Exception directly rather than OSError.
                conversion_errors += (ImageCms.PyCMSError,)
                # Preserve alpha separately; CMS handles RGB/LAB/CMYK color.
                alpha = source.getchannel("A") if "A" in source.getbands() else None
                cms_source = source.convert("RGB") if alpha is not None else None
                converted = ImageCms.profileToProfile(cms_source if cms_source is not None else source,
                    ImageCms.ImageCmsProfile(BytesIO(profile)),
                    ImageCms.createProfile("sRGB"), outputMode="RGB")
                if alpha is not None:
                    converted.putalpha(alpha)
                source.close()
                source = converted
                converted = None
            except conversion_errors as exc:
                warnings.append(f"icc_conversion_unavailable:{type(exc).__name__}")
            finally:
                for temporary in (alpha, cms_source, converted):
                    if temporary is not None:
                        temporary.close()
        elif source.mode == "CMYK":
            warnings.append("cmyk_without_icc_assumed")
        if source.mode.startswith("I;16") or source.mode in ("I", "F"):
            # Pillow RGB conversion clips 16-bit grayscale at 255. Preserve range.
            import numpy as np
            samples = np.asarray(source, dtype=np.float32)
            scale = 65535.0 if source.mode != "F" else 1.0
            converted = Image.fromarray(np.clip(samples / scale * 255, 0, 255).astype("uint8"), "L")
            source.close()
            source = converted
            warnings.append("high_bit_depth_display_conversion")
        if "A" in source.getbands() or "transparency" in source.info:
            rgba = source.convert("RGBA")
            try:
                background = Image.new("RGBA", source.size, (255, 255, 255, 255))
                try:
                    background.alpha_composite(rgba)
                    result = background.convert("RGB")
                finally:
                    background.close()
            finally:
                rgba.close()
        elif source.mode == "RGB":
            # Orientation already gave us an owned RGB image. Transferring it
            # avoids another full-size pixel allocation (about 96 MB at 24 MP).
            result = source
            source = None
        else:
            result = source.convert("RGB")
        # ICC has been applied or explicitly failed; do not apply the old profile twice.
        result.info.clear()
        return result
    finally:
        if source is not None:
            source.close()


def _mark(image, path, source_kind, warnings=(), **extra):
    image.info["decode_evidence"] = dict(version=DECODE_VERSION,
        source_path=str(Path(path).resolve()), source_kind=source_kind,
        source_size=list(image.size), full_resolution=not source_kind.startswith("raw_embedded"),
        color_space="sRGB" if not any("icc_conversion_unavailable" in w or "unverified" in w for w in warnings) else "unverified",
        warnings=list(warnings), **extra)
    note("decode_source", source_kind)
    return image


def load_standard_image(path: Path) -> Image.Image:
    path = Path(path)
    register_image_codecs()
    status = _codec_status.get(path.suffix.lower())
    if status and not status["available"]:
        raise CodecUnavailable(status["detail"])
    def decode():
        warnings = []
        try:
            with Image.open(path) as source:
                _check_dimensions(source.size)
                source.load()  # Detect truncated data before considering JPG usable.
                image = _normalize_rgb(source, warnings)
            return _mark(image, path, "standard_image", warnings)
        except DecodeError:
            raise
        except (OSError, ValueError, SyntaxError, RuntimeError) as exc:
            raise DecodeError(f"无法读取图片 {path.name}：{type(exc).__name__}") from exc
    return _cached_load(path, "standard", decode)


@contextmanager
def _raw_handle(path, backend):
    # rawpy.imread eagerly unpacks the sensor, even for a thumbnail request.
    # Opening only the container lets extract_thumb avoid that intermediate work.
    if hasattr(backend, "RawPy"):
        with backend.RawPy() as raw:
            raw.open_file(str(path))
            yield raw, True
    else:  # Compatibility with lightweight/injected decoder implementations.
        with backend.imread(str(path)) as raw:
            yield raw, False


def _raw_full(raw, path, warnings, backend, unpack=False):
    sizes = raw.sizes
    width, height = getattr(sizes, "width", 0), getattr(sizes, "height", 0)
    if width and height:
        _check_dimensions((width, height))
    kwargs = dict(use_camera_wb=True, half_size=False, no_auto_bright=True, output_bps=8)
    if hasattr(backend, "ColorSpace"):
        kwargs["output_color"] = backend.ColorSpace.sRGB
    with operation("raw_full_decode"):
        if unpack:
            raw.unpack()
        rgb = raw.postprocess(**kwargs)
    image = Image.fromarray(rgb)
    try:
        _check_dimensions(image.size)
        # LibRaw postprocess already applies orientation; never transpose again.
        return _mark(image, path, "raw_full_decode", warnings)
    except BaseException:
        image.close()
        raise


def load_raw_image(path: Path, *, full_raw=False, apply_orientation=True, backend=None) -> Image.Image:
    path = Path(path)
    injected = backend is not None and backend is not rawpy
    backend = backend if backend is not None else rawpy
    if backend is None:
        raise CodecUnavailable("RAW 解码器不可用；请安装 raw 可选依赖")
    def decode():
        warnings = []
        try:
            with _raw_handle(path, backend) as (raw, needs_unpack):
                if full_raw:
                    return _raw_full(raw, path, warnings, backend, unpack=needs_unpack)
                try:
                    with operation("raw_embedded_preview"):
                        thumb = raw.extract_thumb()
                        if thumb.format == backend.ThumbFormat.JPEG:
                            with Image.open(BytesIO(thumb.data)) as source:
                                _check_dimensions(source.size)
                                source.load()
                                image = source.copy()
                            kind = "raw_embedded_jpeg"
                        elif thumb.format == backend.ThumbFormat.BITMAP:
                            image = Image.fromarray(thumb.data)
                            kind = "raw_embedded_bitmap"
                        else:
                            raise ValueError("unsupported_embedded_preview")
                        try:
                            _check_dimensions(image.size)
                            if apply_orientation:
                                oriented = orient_raw_thumbnail(image, raw.sizes.flip)
                                if oriented is not image:
                                    image.close()
                                    image = oriented
                            normalized = _normalize_rgb(image, warnings, orient=False)
                        finally:
                            image.close()
                        return _mark(normalized, path, kind, warnings)
                except DecodeError:
                    raise
                except Exception as exc:
                    # rawpy thumbnail exceptions inherit Exception directly.
                    warnings.append(f"embedded_preview_unavailable:{type(exc).__name__}")
                    # Deliberately no half-size intermediate layer.
                    return _raw_full(raw, path, warnings, backend, unpack=needs_unpack)
        except DecodeError:
            raise
        except Exception as exc:
            raise DecodeError(f"RAW 无法解码 {path.name}（机型/压缩不支持或文件损坏）：{type(exc).__name__}") from exc
    if injected:
        with _decode_slots:
            return decode()
    return _cached_load(path, ("raw_full" if full_raw else "raw_preview", apply_orientation), decode)


def _raw_path(asset):
    path = getattr(asset, "raw_path", None)
    primary = Path(asset.primary_path)
    return Path(path) if path else (primary if primary.suffix.lower() in RAW_EXTENSIONS else None)


def _initial_image(asset, *, apply_orientation=True):
    raw_path = _raw_path(asset)
    jpg_path = getattr(asset, "jpg_path", None)
    if jpg_path is not None:
        try:
            image = load_standard_image(Path(jpg_path))
            image.info["decode_evidence"]["source_kind"] = "paired_jpeg" if raw_path else "standard_image"
            return image
        except (OSError, ValueError) as exc:
            if raw_path is None:
                raise
            image = load_raw_image(raw_path, apply_orientation=apply_orientation)
            image.info["decode_evidence"]["warnings"].append(f"paired_jpeg_unreadable:{type(exc).__name__}")
            return image
    return (load_raw_image(raw_path, apply_orientation=apply_orientation) if raw_path
            else load_standard_image(Path(asset.primary_path)))


def evidence_is_sufficient(image, evidence_regions=(), min_region_pixels=96):
    """Pixel coverage only; absence/blur/confidence require caller evidence."""
    if min_region_pixels <= 0:
        raise ValueError("min_region_pixels must be positive")
    width, height = image.size
    regions = tuple(evidence_regions or ())
    if not regions:
        return min(width, height) >= MIN_PREVIEW_SIDE
    for region in regions:
        if len(region) != 4 or not all(math.isfinite(float(v)) for v in region):
            raise ValueError("evidence_regions must contain finite normalized x/y/w/h boxes")
        x, y, w, h = map(float, region)
        actual_w = max(0, min(1., x + w) - max(0., x)) * width
        actual_h = max(0, min(1., y + h) - max(0., y)) * height
        if min(actual_w, actual_h) < min_region_pixels:
            return False
    return True


def refine_analysis_image(asset, image, *, evidence_regions=(), min_region_pixels=96,
                          insufficient_evidence=False):
    """Return the same image or an owned RAW upgrade; caller closes replaced image."""
    sufficient = evidence_is_sufficient(image, evidence_regions, min_region_pixels)
    metadata = image.info.setdefault("decode_evidence", {})
    metadata["evidence_sufficient"] = sufficient and not insufficient_evidence
    raw_path = _raw_path(asset)
    if (raw_path is not None and metadata.get("source_kind") != "raw_full_decode"
            and not metadata.get("raw_upgrade_attempted")
            and (not sufficient or insufficient_evidence)):
        metadata["raw_upgrade_attempted"] = True
        try:
            upgraded = load_raw_image(raw_path, full_raw=True)
        except DecodeError as exc:
            metadata.setdefault("warnings", []).append(f"full_raw_unavailable:{type(exc).__name__}")
            metadata["evidence_sufficient"] = False
            return image
        previous_warnings = metadata.get("warnings", [])
        upgrade_metadata = upgraded.info["decode_evidence"]
        upgrade_metadata["warnings"] = previous_warnings + upgrade_metadata["warnings"]
        upgrade_metadata["escalation_reason"] = "insufficient_detail_evidence" if insufficient_evidence else "insufficient_region_pixels"
        upgrade_metadata["evidence_sufficient"] = evidence_is_sufficient(upgraded, evidence_regions, min_region_pixels)
        return upgraded
    return image


def load_analysis_image(asset, *, evidence_regions=(), min_region_pixels=96,
                        insufficient_evidence=False) -> Image.Image:
    image = _initial_image(asset)
    try:
        refined = refine_analysis_image(asset, image, evidence_regions=evidence_regions,
            min_region_pixels=min_region_pixels, insufficient_evidence=insufficient_evidence)
    except Exception:
        image.close()
        raise
    if refined is not image:
        image.close()
    return refined


def load_display_image(asset, *, full_raw=False) -> tuple[Image.Image, str]:
    """Viewing never changes analysis. Full RAW is an explicit viewer request."""
    raw_path = _raw_path(asset)
    image = load_raw_image(raw_path, full_raw=True) if full_raw and raw_path else _initial_image(asset)
    return image, image.info["decode_evidence"]["source_kind"]
