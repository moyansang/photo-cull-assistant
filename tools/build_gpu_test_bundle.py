"""Build an offline Windows bundle from committed code and allowlisted runtime files."""
import argparse
import hashlib
import importlib.metadata as metadata
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile


def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--photos',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1];dest=args.output.resolve()
    dest.mkdir(parents=True,exist_ok=False)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    archive=subprocess.check_output(['git','archive','--format=zip','HEAD','src','experiments/gpu_raw',
                                      'body_models','pyproject.toml','README.md'],cwd=repo)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:z.extractall(dest/'code')
    for name in ['run.py','START.bat','README.txt','CODEX_PROMPT.txt']:
        shutil.copy2(repo/'experiments/gpu_raw/portable'/name,dest/name)
    runtime=dest/'runtime';runtime.mkdir()
    cache=repo/'build/python-3.12.10-embed-amd64.zip'
    url='https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
    if not cache.exists():urllib.request.urlretrieve(url,cache)
    with zipfile.ZipFile(cache) as z:z.extractall(runtime)
    (runtime/'python312._pth').write_text('python312.zip\n.\n../libraries\n../code/src\n../code/experiments/gpu_raw\nimport site\n','utf-8')
    wanted={'numpy','pillow','opencv-python-headless','rawpy','exifread','pytest','pluggy','iniconfig',
            'pygments','packaging','colorama','pyopencl','pytools','siphash24','platformdirs','typing-extensions'}
    installed={}
    search=[repo/'.venv/Lib/site-packages',repo/'build/gpu-memory-prototype/deps']
    for d in metadata.distributions(path=[str(p) for p in search]):
        name=d.metadata['Name'].lower().replace('_','-')
        if name not in wanted:continue
        installed[name]=d.version
        for f in d.files or []:
            if '..' in f.parts or '__pycache__' in f.parts or str(f).endswith(('.pyc','.pth','direct_url.json')):continue
            src=Path(d.locate_file(f));target=dest/'libraries'/str(f)
            if src.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
    if set(installed)!=wanted:raise RuntimeError('Missing packages: '+str(wanted-set(installed)))
    # Retain upstream licenses/source notices for all model files.
    models=json.loads((repo/'body_models/manifest.json').read_text('utf-8'))['models']
    for model in models:
        p=repo/'build/body_models'/model['filename']
        if sha(p)!=model['sha256']:raise RuntimeError('Model checksum mismatch')
        target=dest/'code/build/body_models'/p.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    originals=sorted(args.photos.resolve().glob('*.RW2'))
    if len(originals)!=80:raise ValueError('Expected the original 80 RW2 samples')
    (dest/'photos').mkdir()
    for i,p in enumerate(originals):
        target=dest/'photos'/p.name;shutil.copy2(p,target)
        if sha(p)!=sha(target):raise RuntimeError('Photo copy integrity failure')
        if i%20==19:print('Copied photos:',i+1,flush=True)
    manifest=dict(source_commit=commit,python_url=url,python_zip_sha256=sha(cache),packages=installed,
                  files={p.relative_to(dest).as_posix():sha(p) for p in dest.rglob('*') if p.is_file()})
    (dest/'bundle-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    print('Prepared:',dest,flush=True)


if __name__=='__main__':main()
