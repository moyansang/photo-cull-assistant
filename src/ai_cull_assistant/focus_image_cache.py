"""Reusable native evidence independent of API provider or review success."""
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from PIL import Image

from .focus_audit import audit_root


def cached_focus_images(asset, settings, cache_dir, face, *, subject=None,
                        include_body=True, decoded_image=None):
    from . import ai_focus
    from .ai_project import atomic_json
    if subject is None and face:
        subject = ai_focus.detail_features(asset, settings)
    preview = Path(asset.preview_path) if asset.preview_path else None
    geometry = None
    if preview is not None and preview.is_file():
        with Image.open(preview) as image:
            geometry = image.size
    identity = dict(version=1, source=ai_focus._source_identity(asset, face),
                    geometry=geometry, preview_version=preview.parent.name if preview else None,
                    landmarks=getattr(subject, 'landmarks', None),
                    crop=settings.photos.get(settings.key(asset), {}),
                    body=(asset.clarity_evidence or {}).get('body') if include_body else None,
                    include_body=include_body)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = audit_root(cache_dir) / 'native-cache' / digest
    manifest = directory / 'images.json'
    try:
        data = json.loads(manifest.read_text('utf-8'))
        paths = []
        for item in data['images']:
            name = item['name']
            if Path(name).name != name or not name.endswith('.png'):
                raise ValueError('invalid image name')
            path = directory / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('image cache changed')
            paths.append(path)
        if paths:
            return paths, bool(data['face_found'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    directory.parent.mkdir(parents=True, exist_ok=True)
    stage = directory.with_name(digest + '.tmp-' + uuid.uuid4().hex)
    try:
        paths, found = ai_focus._prepare_focus_images(
            asset, settings, stage, face, subject_override=subject,
            include_body=include_body, decoded_image=decoded_image)
        entries = [dict(name=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
        directory.mkdir(exist_ok=True)
        for path in paths:
            path.replace(directory / path.name)
        atomic_json(manifest, dict(images=entries, face_found=found))
        return [directory / item['name'] for item in entries], found
    finally:
        if stage.resolve().parent != directory.parent.resolve():
            raise ValueError("临时细节缓存路径异常")
        if stage.is_dir():
            # Only our unique staging directory, never original photographs.
            shutil.rmtree(stage)


def warm_focus_images(asset, settings, cache_dir):
    """Save pending evidence while scan's full-size decode is still available."""
    from .shared_decode import decoded_image
    from .lightroom_results import focus_review_status
    from .crop_settings import CropSettings
    from .ai_focus import _subject_faces
    image = decoded_image(asset)
    if image is None or not focus_review_status(asset):
        return
    settings = settings or CropSettings()
    try:
        subjects = _subject_faces(asset, settings)
        for subject, face in subjects:
            cached_focus_images(asset, settings, cache_dir, face, subject=subject,
                                include_body=len(subjects) == 1, decoded_image=image)
    except (OSError, ValueError, RuntimeError) as exc:
        # Optional cache failure must not change the clarity verdict or stop scanning.
        from .scan_diagnostics import note
        note('focus_image_cache_error', str(exc))


def clear_focus_images(cache_dir):
    root = audit_root(cache_dir).resolve()
    target = root / 'native-cache'
    if not target.exists():
        return
    if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise ValueError('原尺寸细节缓存目录包含链接，未清理')
    if target.resolve().parent != root:
        raise ValueError('原尺寸细节缓存目录不在工作区内')
    shutil.rmtree(target)
