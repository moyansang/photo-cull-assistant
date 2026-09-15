"""Bounded GPU slots inside the existing bounded photo executor.

All command queues share a context/program; each owns kernels and buffers.
Only the benchmark installs process-wide hooks, once before workers start.
"""
from queue import Queue
from threading import Lock, local
from time import perf_counter

from backend import Developer
from hybrid import choose_result, review_reasons
from ai_cull_assistant.face_focus import assess_asset_focus, load_full_image
from ai_cull_assistant.shared_decode import shared_decode


class DeveloperPool:
    def __init__(self, cache_dir, slots):
        if slots not in (1,2):
            raise ValueError('Choose one or two experimental GPU slots')
        self.backends=[]; self.available=Queue(maxsize=slots)
        self.lock=Lock(); self.records=[]; self.active=0; self.peak_active=0
        try:
            for i in range(slots):
                b=Developer(cache_dir,shared=self.backends[0] if i else None,slots=slots)
                self.backends.append(b); self.available.put((i,b))
        except Exception:
            self.close(); raise
        self.device=self.backends[0].device

    def run(self, *args):
        start=perf_counter(); i,b=self.available.get()
        waited=perf_counter()-start
        try:
            with self.lock:
                self.active+=1;self.peak_active=max(self.active,self.peak_active)
            result,profile=b.run(*args)
            profile={**profile,'slot':i,'queue_wait_seconds':waited}
            with self.lock:self.records.append(profile)
            return result,profile
        finally:
            with self.lock:self.active-=1
            self.available.put((i,b))

    def summary(self):
        # Device profiling timestamps share the same device clock. Actual
        # kernel overlap is distinct from simultaneous host run() calls.
        intervals=sorted((a,z,r['slot']) for r in self.records for a,z in r['kernel_intervals_ns'])
        overlap=sum(max(0,min(z,d)-max(a,c)) for j,(a,z,s) in enumerate(intervals)
                    for c,d,t in intervals[j+1:] if t!=s and c<z)
        return dict(slots=len(self.backends),calls=len(self.records),peak_active=self.peak_active,
            cross_slot_kernel_overlap_seconds=overlap/1e9,
            allocated_buffer_bytes=sum(sum(x.size for x in b.buffers) for b in self.backends),
            allocations=sum(b.allocations for b in self.backends))

    def close(self):
        for b in self.backends:b.close()


class ConcurrentHybrid:
    def __init__(self, gpu_loader):
        self.gpu_loader=gpu_loader; self.state=local()

    def load(self, asset):
        return load_full_image(asset) if getattr(self.state,'cpu',False) else self.gpu_loader(asset)

    def assess(self, asset, *, crop_settings=None, cache_dir=None):
        gpu=assess_asset_focus(asset,crop_settings=crop_settings,cache_dir=None)
        def cpu_check():
            previous=getattr(self.state,'cpu',False)
            self.state.cpu=True
            try:
                with shared_decode(asset):
                    return assess_asset_focus(asset,crop_settings=crop_settings,cache_dir=None)
            finally:self.state.cpu=previous
        return choose_result(gpu,review_reasons(gpu),cpu_check)
