"""At most ten explicit local RAW samples. No API, no workspace changes."""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np
from PIL import Image
import rawpy
from backend import Developer, parameters
from ai_cull_assistant.face_focus import assess_asset_focus
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.preview import build_preview


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    samples=json.loads(args.samples.read_text('utf-8'))
    if not 1 <= len(samples) <= 10:
        parser.error('Choose 1 to 10 explicit samples')
    root=args.output.resolve()
    paths=[Path(s['path']).resolve(strict=True) for s in samples]
    if len(set(paths))!=len(paths) or any(p.suffix.lower()!='.rw2' for p in paths):
        parser.error('Use unique RW2 files')
    if any(p.parent==root or p.parent in root.parents for p in paths):
        parser.error('Output must not be inside a photo source directory')
    root.mkdir(parents=True,exist_ok=False)
    before={str(p):digest(p) for p in paths}
    start=perf_counter()
    backend=Developer(root/'cl-cache')
    report=dict(gpu=backend.device.name, driver=backend.device.driver_version,
                global_memory=int(backend.device.global_mem_size),
                initialization_seconds=perf_counter()-start,rawpy=rawpy.__version__,rows=[])
    try:
        for index,(sample,path) in enumerate(zip(samples,paths)):
            asset=PhotoAsset(path.stem,path,path,path,None,datetime.now(),path.suffix)
            build_preview(asset,root/'previews')
            row=dict(name=path.name,label=sample['label'],order='gpu-first' if index%2==0 else 'cpu-first')
            try:
                start=perf_counter()
                raw=rawpy.imread(str(path))
                row['open_seconds']=perf_counter()-start
                with raw:
                    start=perf_counter()
                    mosaic=raw.raw_image_visible
                    row['unpack_seconds']=perf_counter()-start
                    start=perf_counter()
                    pattern,p=parameters(raw)
                    flip=raw.sizes.flip
                    metadata_seconds=perf_counter()-start
                    # postprocess may update LibRaw metadata. Snapshot it first
                    # so alternating benchmark order cannot change GPU settings.
                    mosaic_digest=hashlib.sha256(np.ascontiguousarray(mosaic)).hexdigest()
                    def gpu():
                        start=perf_counter()
                        array,timing=backend.run(mosaic,pattern,p,flip)
                        image=Image.fromarray(array)
                        row['gpu_seconds']=perf_counter()-start+metadata_seconds
                        row['gpu_profile']=timing
                        return image
                    def cpu():
                        start=perf_counter()
                        image=Image.fromarray(raw.postprocess(use_camera_wb=True,half_size=False,
                                                no_auto_bright=True,output_bps=8))
                        row['cpu_seconds']=perf_counter()-start
                        return image
                    if index%2==0: gpu_image,cpu_image=gpu(),cpu()
                    else: cpu_image,gpu_image=cpu(),gpu()
                    if hashlib.sha256(np.ascontiguousarray(mosaic)).hexdigest()!=mosaic_digest:
                        raise ValueError('RAW sensor buffer changed during postprocess')
                row['cpu_size']=list(cpu_image.size)
                row['gpu_size']=list(gpu_image.size)
                row['same_geometry']=cpu_image.size==gpu_image.size
                for name,image in [('cpu',cpu_image),('gpu',gpu_image)]:
                    with patch('ai_cull_assistant.face_focus.load_full_image',side_effect=lambda _:image.copy()):
                        focus=assess_asset_focus(asset)
                    row[name+'_focus']=asdict(focus)
                    box=focus.face_box
                    if box:
                        x,y,w,h=box
                        image.crop((x,y,x+w,y+h)).save(root/(path.stem+'-'+name+'-face.png'))
                    thumb=image.copy();thumb.thumbnail((600,600));thumb.save(root/(path.stem+'-'+name+'.jpg'));thumb.close()
                if row['same_geometry']:
                    # Chunked to avoid an extra full-size float RGB array.
                    a,b=np.asarray(cpu_image),np.asarray(gpu_image)
                    row['rgb_mean_absolute_difference']=float(sum(
                        np.abs(a[y:y+128].astype(np.int16)-b[y:y+128]).sum(dtype=np.float64)
                        for y in range(0,len(a),128))/a.size)
                row['same_decision']=row['cpu_focus']['reason']==row['gpu_focus']['reason']
                cpu_image.close();gpu_image.close()
            except Exception as exc:
                row['error']=type(exc).__name__+': '+str(exc)
            report['rows'].append(row)
            (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
            print(index+1,path.name,row.get('error','completed'),flush=True)
    finally:
        backend.close()
        report['originals_unchanged']=all(digest(Path(p))==value for p,value in before.items())
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    if not report['originals_unchanged'] or any('error' in r for r in report['rows']):
        raise RuntimeError('Benchmark incomplete or integrity failure; inspect report')


if __name__=='__main__':
    main()
