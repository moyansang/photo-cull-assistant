"""Fresh isolated 1..80 RW2 scan; experimental GPU override exists only here."""
import argparse
from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path
import threading
from time import perf_counter
from unittest.mock import patch

from PIL import Image
import rawpy
from backend import Developer, parameters
from hybrid import assess_hybrid
from pipeline import ConcurrentHybrid, DeveloperPool
from monitor import ResourceMonitor
from ai_cull_assistant.face_focus import assess_asset_focus
from ai_cull_assistant.face_focus import load_full_image
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import IMAGE_EXTENSIONS
from ai_cull_assistant.processing_job import start_job
from ai_cull_assistant.scan_resources import capture_resource_snapshot
from ai_cull_assistant.scan_tuning import ScanHistory
from ai_cull_assistant.scan_timing import measure
from ai_cull_assistant.scan_diagnostics import operation, note


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--backend',choices=['cpu','gpu','hybrid','pipeline'],required=True)
    parser.add_argument('--workers',type=int,choices=[0,1,2,4],default=1,
                        help='0: existing adaptive policy; otherwise fixed ceiling after warmup')
    parser.add_argument('--gpu-slots',type=int,choices=[1,2],default=1)
    args=parser.parse_args()
    if args.backend in {'gpu','hybrid'} and args.workers!=1:
        parser.error('Legacy backends are serial; use pipeline for concurrent photos')
    if args.gpu_slots!=1 and args.backend!='pipeline':
        parser.error('GPU slot count applies only to the pipeline backend')
    source=args.input.resolve(strict=True); root=args.output.resolve()
    if source==root or source in root.parents or root in source.parents:
        parser.error('Use a separate experimental output directory')
    files=sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    if not 1<=len(files)<=80 or any(p.suffix.lower()!='.rw2' for p in files):
        parser.error('Input must contain 1..80 RW2 images only')
    before={str(p):(p.stat().st_size,p.stat().st_mtime_ns) for p in files}
    root.mkdir(parents=True,exist_ok=False)
    report=dict(backend=args.backend,resources=asdict(capture_resource_snapshot()),workers=args.workers,
                body_screening=False,rawpy=rawpy.__version__,logs=[])
    backend=None
    monitor=ResourceMonitor();monitor.start()
    start=perf_counter()
    try:
        if args.backend in {'gpu','hybrid'}:
            backend=Developer(root/'cl-cache')
            report['gpu']=backend.device.name
        if args.backend=='pipeline':
            backend=DeveloperPool(root/'cl-cache',args.gpu_slots)
            report['gpu']=backend.device.name
        report['initialization_seconds']=perf_counter()-start
        @measure('decode')
        def gpu_image(asset):
            path=asset.raw_path or asset.primary_path
            if path.suffix.lower()!='.rw2':
                return load_full_image(asset)
            with operation('raw_full_decode'):
                with operation('raw_full_open'):
                    raw=rawpy.imread(str(path))
                with raw:
                    with operation('raw_full_unpack'):
                        mosaic=raw.raw_image_visible
                    with operation('raw_gpu_develop'):
                        pattern,params=parameters(raw)
                        rgb,timing=backend.run(mosaic,pattern,params,raw.sizes.flip)
                        image=Image.fromarray(rgb)
                    note('experimental_raw_backend','opencl-mhc-v1')
                    note('gpu_profile',timing)
                    return image
        def log(line):
            report['logs'].append(line);print(line,flush=True)
        def history(snapshot,body):
            return ScanHistory(snapshot,body,root/'tuning.json')
        options={'technical_screening':True,'body_screening':False}
        concurrent=ConcurrentHybrid(gpu_image)
        concurrency=(patch('ai_cull_assistant.processing_job.AdaptiveConcurrencyPolicy.choose',
                           side_effect=lambda cap:min(args.workers,cap)) if args.workers else nullcontext())
        with patch('ai_cull_assistant.processing_job.ScanHistory',side_effect=history), \
             concurrency, \
             patch('ai_cull_assistant.face_focus.load_full_image',concurrent.load if args.backend=='pipeline' else gpu_image if backend else load_full_image), \
             patch('ai_cull_assistant.face_focus.assess_asset_focus',concurrent.assess if args.backend=='pipeline' else assess_hybrid if args.backend=='hybrid' else assess_asset_focus):
            job=start_job(source,root/'workspace',options,CropSettings(),mode='scan')
            result=job.run(options,CropSettings(),threading.Event(),None,on_log=log)
        report['wall_seconds']=perf_counter()-start
        report['verdicts']=[dict(name=a.primary_path.name,reason=a.screening_reason,
            rejected=a.auto_rejected,face=a.face_found,group=a.group_id,evidence=a.clarity_evidence,
            focus_score=a.focus_score) for a in result.assets]
    finally:
        if isinstance(backend,DeveloperPool):
            report['pool']=backend.summary()
            backend.close()
        elif backend:
            report['buffer_allocations']=backend.allocations
            report['allocated_buffer_bytes']=sum(b.size for b in backend.buffers)
            backend.close()
        report['memory']=monitor.stop()
        report['original_metadata_unchanged']=all(
            (Path(p).stat().st_size,Path(p).stat().st_mtime_ns)==v for p,v in before.items())
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    if not report['original_metadata_unchanged']:
        raise RuntimeError('Source metadata changed')


if __name__=='__main__':
    main()
