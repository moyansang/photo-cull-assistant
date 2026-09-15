"""Sample process RSS and device-wide VRAM; neither is an exact allocation trace."""
import shutil
import subprocess
from threading import Event, Thread
from ai_cull_assistant.scan_resources import capture_resource_snapshot


class ResourceMonitor:
    def start(self):
        self.done=Event();self.rss=[];self.vram=[];self.process=None;self.gpu_thread=None
        def sample():
            while not self.done.is_set():
                self.rss.append(capture_resource_snapshot().process_rss_bytes or 0)
                self.done.wait(.05)
        self.thread=Thread(target=sample,daemon=True);self.thread.start()
        if shutil.which('nvidia-smi'):
            try:
                self.process=subprocess.Popen(['nvidia-smi','--id=0','--query-gpu=memory.used',
                    '--format=csv,noheader,nounits','-lms','200'],stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                def gpu_sample():
                    for line in self.process.stdout:
                        try:self.vram.append(int(line.strip()))
                        except ValueError:pass
                self.gpu_thread=Thread(target=gpu_sample,daemon=True);self.gpu_thread.start()
            except OSError:pass

    def stop(self):
        self.done.set();self.thread.join()
        if self.process:
            if self.process.poll() is None:self.process.terminate()
            self.process.wait(timeout=5)
            if self.gpu_thread:self.gpu_thread.join(timeout=5)
            self.process.stdout.close()
        return dict(process_peak_rss_bytes=max(self.rss,default=0),
                    gpu_device0_baseline_mib=self.vram[0] if self.vram else None,
                    gpu_device0_peak_mib=max(self.vram,default=None),
                    note='50ms RSS and 200ms device0 total VRAM samples; includes other GPU apps, not exact peaks')
