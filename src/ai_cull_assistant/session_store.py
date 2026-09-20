"""Persist completed scan analysis without reopening or decoding photos."""
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json
from .models import PhotoAsset
from .subject import SubjectFeatures
from .workflow import ScanResult
from .ai_project import atomic_json


def save_session(result, fresh=False):
    data=asdict(result)
    data['version']=1
    data['source_stats']={}
    for asset in result.assets:
        for path in asset.rating_target_paths:
            st=path.stat()
            data['source_stats'][str(path.resolve())]=[st.st_size,st.st_mtime_ns]
    cache=result.workspace_dir/"scan-session.json"
    if cache.exists() and not fresh:
        previous=json.loads(cache.read_text("utf-8"))
        if previous.get("source_stats")!=data["source_stats"]:
            raise ValueError("原片已变化，保留旧分析记录，请重新扫描")
    def encode(value):
        if isinstance(value,Path):return str(value.resolve())
        if isinstance(value,datetime):return value.isoformat()
        raise TypeError(type(value).__name__)
    atomic_json(result.workspace_dir/'scan-session.json',json.loads(json.dumps(data,default=encode)))
    # Relative inventory is portable; the active session retains legacy absolute
    # paths for existing code and is rebound by the relocation operation.
    if result.input_dir:
        root = Path(result.input_dir).resolve()
        atomic_json(result.workspace_dir / '.source-location.json', {
            'version': 1, 'input_dir': str(root),
            'files': {str(Path(name).relative_to(root)): stats
                      for name, stats in data['source_stats'].items()},
        })


def source_changes(workspace, input_dir):
    """Compare filenames cheaply, without decoding photos or reading EXIF."""
    from .scanner import iter_image_files
    path = Path(workspace) / 'scan-session.json'
    if not path.exists():
        return [], []
    data = json.loads(path.read_text('utf-8'))
    if Path(data['input_dir']).resolve() != Path(input_dir).resolve():
        return [], []
    if not Path(input_dir).is_dir():
        raise ValueError('照片文件夹不可访问，请检查磁盘连接；原分析记录保留')
    previous = set(data['source_stats'])
    current = {str(p.resolve()) for p in iter_image_files(Path(input_dir))}
    return sorted(current - previous), sorted(previous - current)


def load_session(workspace,input_dir):
    workspace=Path(workspace).resolve()
    path=workspace/'scan-session.json'
    if not path.exists():return None
    data=json.loads(path.read_text('utf-8'))
    if data.pop('version')!=1:raise ValueError('扫描记录版本不支持')
    if not input_dir or Path(data['input_dir']).resolve()!=Path(input_dir).resolve():return None
    if Path(data['workspace_dir']).resolve()!=workspace:return None
    if not Path(input_dir).is_dir():
        raise ValueError('照片文件夹不可访问，请检查磁盘连接；原分析记录保留')
    removed = set()
    for path,expected in data.pop('source_stats').items():
        try:
            st=Path(path).stat()
        except FileNotFoundError:
            removed.add(path)
            continue
        if [st.st_size,st.st_mtime_ns]!=expected:raise ValueError('原照片已修改，请重新扫描')
    assets=[]
    for row in data['assets']:
        # A remaining JPEG after RAW removal is picked up as new scan input.
        if str(Path(row['primary_path']).resolve()) in removed:
            continue
        for field in ('raw_path', 'jpg_path'):
            if row[field] and str(Path(row[field]).resolve()) in removed:
                row[field] = None
        if str(Path(row['display_path']).resolve()) in removed:
            row['display_path'] = row['primary_path']
            row['preview_path'] = None
        for key in ('display_path','primary_path','raw_path','jpg_path','preview_path'):
            row[key]=Path(row[key]) if row[key] else None
        row['captured_at']=datetime.fromisoformat(row['captured_at'])
        if row['subject_features']:
            feature=row['subject_features']
            for key in ('face','head'):
                if feature.get(key):feature[key]=tuple(feature[key])
            if feature.get('landmarks'):
                feature['landmarks']=tuple(tuple(point) for point in feature['landmarks'])
            row['subject_features']=SubjectFeatures(**feature)
        # Preview JPEGs are disposable cache entries.  The source inventory
        # above is the durable validity check; callers rebuild a missing
        # preview lazily with ``ensure_preview`` when it is first displayed.
        assets.append(PhotoAsset(**row))
    data['assets']=assets
    for key in ('preview_dir','contact_dir','workspace_dir','group_store_path','input_dir','screening_results_path'):
        data[key]=Path(data[key]) if data[key] else None
    for key in ('main_pages','rejected_pages'):
        data[key]=[Path(p) for p in data[key]] if data[key] is not None else None
    result = ScanResult(**data)
    if removed:
        from .group_store import save_groups
        result.rejected_count = sum(a.auto_rejected for a in assets)
        result.main_pages = []
        result.rejected_pages = []
        save_groups(assets, result.group_store_path, source='manual', collection_key=str(result.input_dir))
        screening_path = workspace / 'screening_results.json'
        if screening_path.exists():
            screening = json.loads(screening_path.read_text('utf-8'))
            stems = {a.stem for a in assets}
            screening['results'] = {k:v for k,v in screening.get('results', {}).items() if k in stems}
            atomic_json(screening_path, screening)
        project_path = workspace / 'ai_project.json'
        if project_path.exists():
            project = json.loads(project_path.read_text('utf-8'))
            from .ai_project import photo_id
            ids = {photo_id(a) for a in assets}
            project['photos'] = {k:v for k,v in project.get('photos', {}).items() if k in ids}
            project['export_dirty'] = True
            project['export_status'] = '照片清单已变化，需要重新导出'
            atomic_json(project_path, project)
        atomic_json(workspace / 'workflow-stage.json', {'version':1, 'contact_sheets_ready':False})
        save_session(result, fresh=True)
    return result
