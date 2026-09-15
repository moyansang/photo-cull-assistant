"""Offline Windows comparison runner. All output stays in this extracted bundle."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import uuid
import zipfile

ROOT=Path(__file__).resolve().parent
sys.dont_write_bytecode=True


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def run(command,log,env):
    with log.open('w',encoding='utf-8') as out:
        with subprocess.Popen([sys.executable,*command],cwd=ROOT/'code',env=env,
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,encoding='utf-8',errors='replace') as process:
            for line in process.stdout:
                print(line,end='',flush=True);out.write(line);out.flush()
            if process.wait():raise RuntimeError('Test failed; see '+str(log))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--rounds',type=int,choices=[1,2],default=2)
    parser.add_argument('--workers',type=int,choices=[1,2,4],default=4)
    args=parser.parse_args()
    manifest=json.loads((ROOT/'bundle-manifest.json').read_text('utf-8'))
    for name,expected in manifest['files'].items():
        p=(ROOT/name).resolve()
        if ROOT not in p.parents or not p.is_file() or sha(p)!=expected:
            raise RuntimeError('Bundle damaged or changed: '+name)
    source=None;files=[]
    if not args.check:
        bundled=ROOT/'photos'
        source=(args.input or (bundled if bundled.is_dir() else Path(input('RAW folder path: ').strip().strip('"')))).resolve(strict=True)
        if source!=bundled.resolve() and (source==ROOT or ROOT in source.parents or source in ROOT.parents):
            raise ValueError('Keep RAW folder separate from the test bundle')
        from ai_cull_assistant.models import IMAGE_EXTENSIONS
        files=sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
        if not 1<=len(files)<=80 or any(p.suffix.lower()!='.rw2' for p in files):
            raise ValueError('Choose a separate folder with 1..80 RW2 only (no JPG pairs)')
        if len({p.name.casefold() for p in files})!=len(files):
            raise ValueError('Use unique photo filenames for this benchmark')
    session=ROOT/'results'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    session.mkdir(parents=True)
    env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',
        PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',LOCALAPPDATA=str(session/'local-data'),
        AI_CULL_BODY_MODEL_DIR=str(ROOT/'code/build/body_models'))
    os.environ.update({k:env[k] for k in ['AI_CULL_BODY_MODEL_DIR','LOCALAPPDATA']})
    print('Output:',session,flush=True)
    import pyopencl as cl
    from ai_cull_assistant.scan_resources import capture_resource_snapshot
    from dataclasses import asdict
    hardware=dict(cpu=os.environ.get('PROCESSOR_IDENTIFIER',''),resources=asdict(capture_resource_snapshot()),
        opencl=[dict(platform=p.name,name=d.name,driver=d.driver_version,memory_bytes=int(d.global_mem_size))
                for p in cl.get_platforms() for d in p.get_devices(device_type=cl.device_type.GPU)])
    print(json.dumps(hardware,ensure_ascii=False,indent=2))
    if not any('NVIDIA CUDA' in x['platform'] for x in hardware['opencl']):
        raise RuntimeError('Native NVIDIA OpenCL GPU unavailable; check NVIDIA driver')
    (session/'hardware.json').write_text(json.dumps(hardware,ensure_ascii=False,indent=2),'utf-8')
    # Fail rather than presenting a skipped GPU test as success.
    run(['-m','pytest','experiments/gpu_raw','-q'],session/'self-test.log',env)
    if args.check:
        print('Self-test complete. No photos scanned.');return
    originals={str(p.relative_to(source)):sha(p) for p in files}
    rows=[];baseline=None
    for round_index in range(args.rounds):
        order=['cpu','gpu1','gpu2'] if round_index==0 else ['gpu2','gpu1','cpu']
        for mode in order:
            name=f'r{round_index+1}-{mode}';output=session/name
            print('\nStarting '+name,flush=True)
            command=['experiments/gpu_raw/benchmark_scan.py','--input',str(source),
                     '--output',str(output),'--workers',str(args.workers),
                     '--backend','cpu' if mode=='cpu' else 'pipeline']
            if mode!='cpu':command+=['--gpu-slots',mode[-1]]
            run(command,session/(name+'.log'),env)
            report=json.loads((output/'report.json').read_text('utf-8'))
            verdicts={r['name']:{k:r[k] for k in ['reason','rejected','face','group']} for r in report['verdicts']}
            if len(verdicts)!=len(files):raise ValueError('Incomplete results or duplicate basenames')
            if baseline is None:baseline=verdicts
            differences=[n for n,v in verdicts.items() if v!=baseline.get(n)]
            errors=[r['name'] for r in report['verdicts'] if 'cpu_error' in (r['evidence'] or {}).get('hybrid',{})]
            row=dict(run=name,mode=mode,wall_seconds=report['wall_seconds'],memory=report['memory'],
                pool=report.get('pool'),differences=differences,cpu_errors=errors,
                original_metadata_unchanged=report['original_metadata_unchanged'])
            rows.append(row)
            (session/'partial-summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf-8')
    unchanged=all(sha(source/n)==h for n,h in originals.items())
    means={m:statistics.mean(r['wall_seconds'] for r in rows if r['mode']==m) for m in ['cpu','gpu1','gpu2']}
    summary=dict(source_commit=manifest['source_commit'],hardware=hardware,count=len(files),workers=args.workers,
        rounds=args.rounds,original_sha256_unchanged=unchanged,sample_sha256=originals,means=means,runs=rows)
    (session/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
    valid=unchanged and all(not r['differences'] and not r['cpu_errors'] and r['original_metadata_unchanged'] for r in rows)
    lines=['# GPU 对照测试',f'照片：{len(files)}；在途上限：{args.workers}；轮数：{args.rounds}',
           f'原片 SHA-256 未变：{unchanged}；结果一致且无复查错误：{valid}',
           '', '| 方案 | 平均秒数 |', '|---|---:|']
    lines += [f'| {m} | {t:.3f} |' for m,t in means.items()]
    lines += ['', '详细结果见 summary.json。这里只比较速度与 CPU 一致性，不证明照片判断都正确。',
              '显存遥测为设备0总占用；多显卡时核对设备。当前仍按批调度，未接入正式EXE。']
    (session/'summary.md').write_text('\n'.join(lines),'utf-8')
    with zipfile.ZipFile(session/'feedback.zip','w',zipfile.ZIP_DEFLATED) as z:
        for n in ['summary.json','summary.md','hardware.json','self-test.log']:z.write(session/n,n)
    print('\n'.join(lines));print('\nSend this report:',session/'feedback.zip')
    if not valid:raise RuntimeError('Comparison mismatch; retain reports, do not enable production GPU')


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('\nERROR:',exc,file=sys.stderr);sys.exit(1)
