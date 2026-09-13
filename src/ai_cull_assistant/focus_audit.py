"""Compressed, content-addressed evidence outside disposable analysis caches."""
from pathlib import Path
import hashlib
import json
import zipfile


def audit_root(cache_dir):
    cache = Path(cache_dir)
    workspace = cache.parent.parent if cache.name == 'analysis' and cache.parent.name == 'cache' else cache.parent
    return workspace / 'focus-evidence'


def record_inputs(cache_dir, digest, images, prompt, profile, version):
    from .ai_project import atomic_json
    root = audit_root(cache_dir)
    blobs = root / 'blobs'
    blobs.mkdir(parents=True, exist_ok=True)
    entries = []
    for image in images:
        payload = Path(image).read_bytes()
        identity = hashlib.sha256(payload).hexdigest()
        target = blobs / (identity + '.zip')
        if not target.exists():
            temporary = target.with_suffix('.tmp')
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('image' + Path(image).suffix.lower(), payload)
            temporary.replace(target)
        entries.append(dict(name=Path(image).name, sha256=identity, bytes=len(payload)))
    manifest = dict(version=version, images=entries, prompt=prompt,
                    profile={k:profile[k] for k in ('model','base_url','preset_id') if k in profile})
    target = root / 'requests' / (digest + '.json')
    atomic_json(target, manifest)
    return str(target.relative_to(root.parent))


def record_response(cache_dir, digest, result):
    from .ai_project import atomic_json
    root = audit_root(cache_dir)
    path = root / 'requests' / (digest + '.json')
    document = json.loads(path.read_text('utf-8'))
    document['response'] = result
    atomic_json(path, document)
