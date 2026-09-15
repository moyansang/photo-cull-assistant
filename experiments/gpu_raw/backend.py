"""Research-only resident OpenCL RAW development. Never imported by the app."""
from pathlib import Path
from time import perf_counter

import numpy as np


def parameters(raw):
    """Restricted RGB Bayer metadata path; reject unsupported sensors explicitly."""
    s = raw.sizes
    if raw.raw_pattern is None or raw.raw_pattern.shape != (2, 2):
        raise ValueError('Only 2x2 Bayer RAW is supported by this experiment')
    if raw.color_desc != b'RGBG' or raw.num_colors != 3 or s.pixel_aspect != 1:
        raise ValueError('Only square-pixel RGBG three-color sensors are supported')
    if s.flip not in (0, 3, 5, 6):
        raise ValueError('Unsupported orientation')
    pattern = np.array([[raw.raw_color(s.top_margin+y, s.left_margin+x)
                         for x in range(2)] for y in range(2)], np.int32).ravel()
    if sorted(pattern.tolist()) not in ([0, 1, 1, 2], [0, 1, 2, 3]):
        raise ValueError('Unsupported Bayer layout')
    black = np.asarray(raw.black_level_per_channel, np.float64)
    # Restrict this prototype to uniform black levels; no tiled/optical black model.
    if np.ptp(black) != 0 or raw.white_level <= black[0]:
        raise ValueError('Nonuniform or invalid black level needs a dedicated path')
    wb = np.array(raw.camera_whitebalance, dtype=np.float64)
    if wb[3] == 0:
        wb[3] = wb[1]
    if not np.isfinite(wb).all() or np.any(wb <= 0):
        raise ValueError('Missing camera white balance')
    gains = wb / wb.min() / (raw.white_level - black[0])
    # XYZ-to-camera metadata combined with linear sRGB-to-XYZ. Normalize
    # neutral response, then invert to obtain white-balanced camera-to-sRGB.
    xyz_from_rgb = np.array([[.412453,.357580,.180423],
                             [.212671,.715160,.072169], [.019334,.119193,.950227]])
    cam = np.asarray(raw.rgb_xyz_matrix[:3], np.float64) @ xyz_from_rgb
    neutral = cam.sum(axis=1)
    if not np.isfinite(cam).all() or np.any(neutral <= 0):
        raise ValueError('Invalid color matrix')
    cam /= neutral[:, None]
    if np.linalg.cond(cam) > 100:
        raise ValueError('Unstable color matrix')
    matrix = np.linalg.inv(cam)
    return pattern, np.concatenate((black, gains, matrix.ravel())).astype(np.float32)


def orient(rgb, flip):
    if flip not in (0, 3, 5, 6):
        raise ValueError('Unsupported orientation')
    return np.ascontiguousarray(np.rot90(rgb, {0:0, 3:2, 5:1, 6:3}[flip]))


class Developer:
    def __init__(self, cache_dir, *, shared=None, slots=1):
        import pyopencl as cl
        self.cl = cl
        devices = []
        for platform in cl.get_platforms():
            if 'NVIDIA CUDA' not in platform.name:
                continue
            devices.extend(platform.get_devices(device_type=cl.device_type.GPU))
        if not devices:
            raise RuntimeError('No native NVIDIA OpenCL GPU; no silent CPU fallback in benchmark')
        self.device = max(devices, key=lambda d:d.global_mem_size)
        self.context = shared.context if shared else cl.Context([self.device])
        self.memory_budget = self.device.global_mem_size // 3 // slots
        self.queue = cl.CommandQueue(self.context, properties=cl.command_queue_properties.PROFILING_ENABLE)
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        self.program = shared.program if shared else cl.Program(
            self.context, Path(__file__).with_name('develop.cl').read_text('utf-8')).build(cache_dir=str(cache_dir))
        self.normalize = cl.Kernel(self.program, 'normalize_raw')
        self.develop = cl.Kernel(self.program, 'develop')
        self.shape = None
        self.buffers = []
        self.allocations = 0

    def _allocate(self, shape):
        if self.shape == shape:
            return
        pixels = int(np.prod(shape))
        sizes = [pixels*2, pixels*4, pixels*3, 16, 68]
        if pixels < 16 or max(sizes) > self.device.max_mem_alloc_size or sum(sizes) > self.memory_budget:
            raise MemoryError('Prototype GPU memory budget exceeded')
        self.queue.finish()
        for buffer in self.buffers:
            buffer.release()
        self.buffers = []
        self.shape = None
        try:
            for size in sizes:
                self.buffers.append(self.cl.Buffer(self.context, self.cl.mem_flags.READ_WRITE, size))
        except Exception:
            for buffer in self.buffers:
                buffer.release()
            self.buffers = []
            raise
        self.shape = shape
        self.allocations += 1

    def run(self, mosaic, pattern, params, flip=0):
        start = perf_counter()
        mosaic = np.ascontiguousarray(mosaic, dtype=np.uint16)
        h, w = mosaic.shape
        if min(h,w) < 4:
            raise ValueError('Image too small for 5x5 interpolation')
        pattern = np.ascontiguousarray(pattern, np.int32).reshape(4)
        params = np.ascontiguousarray(params, np.float32).reshape(17)
        if not np.isfinite(params).all() or np.any(pattern < 0) or np.any(pattern > 3):
            raise ValueError('Invalid kernel parameters')
        self._allocate(mosaic.shape)
        raw, normalized, output, pat, par = self.buffers
        cl, q = self.cl, self.queue
        uploads = [cl.enqueue_copy(q, raw, mosaic, is_blocking=False),
                   cl.enqueue_copy(q, pat, pattern, is_blocking=False),
                   cl.enqueue_copy(q, par, params, is_blocking=False)]
        first = self.normalize(q,(w,h),None,raw,normalized,pat,par,np.int32(w),np.int32(h))
        second = self.develop(q,(w,h),None,normalized,output,pat,par,np.int32(w),np.int32(h))
        result = np.empty((h,w,3), np.uint8)
        download = cl.enqueue_copy(q, result, output, is_blocking=False)
        download.wait()
        result = orient(result,flip)
        elapsed = perf_counter()-start
        seconds = lambda event: (event.profile.end-event.profile.start)*1e-9
        return result, dict(wall_seconds=elapsed, upload_seconds=sum(map(seconds,uploads)),
                            normalize_seconds=seconds(first), develop_seconds=seconds(second),
                            download_seconds=seconds(download), allocations=self.allocations,
                            allocated_bytes=sum(b.size for b in self.buffers),
                            kernel_intervals_ns=[(e.profile.start,e.profile.end) for e in (first,second)])

    def close(self):
        self.queue.finish()
        for buffer in self.buffers:
            buffer.release()
        self.buffers = []
        self.shape = None
