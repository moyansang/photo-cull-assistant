"""Run after bundling, on a clean dist directory, before ZIP creation."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from ai_cull_assistant.updater import managed_name, MANIFEST
from ai_cull_assistant.version import VERSION
root=Path(sys.argv[1])
files={}
for p in root.rglob('*'):
    if p.is_file():
        name=p.relative_to(root).as_posix()
        if name==MANIFEST: continue
        if not managed_name(name): raise SystemExit('拒绝打包用户文件：'+name)
        files[name]=hashlib.sha256(p.read_bytes()).hexdigest()
(root/MANIFEST).write_text(json.dumps(dict(format='photo-cull-program-v1',version=VERSION,files=files),ensure_ascii=False,indent=2),encoding='utf-8')
