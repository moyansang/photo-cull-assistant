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


def load_session(workspace,input_dir):
    workspace=Path(workspace).resolve()
    path=workspace/'scan-session.json'
    if not path.exists():return None
    data=json.loads(path.read_text('utf-8'))
    if data.pop('version')!=1:raise ValueError('扫描记录版本不支持')
    if not input_dir or Path(data['input_dir']).resolve()!=Path(input_dir).resolve():return None
    if Path(data['workspace_dir']).resolve()!=workspace:return None
    for path,expected in data.pop('source_stats').items():
        st=Path(path).stat()
        if [st.st_size,st.st_mtime_ns]!=expected:raise ValueError('原照片已修改，请重新扫描')
    assets=[]
    for row in data['assets']:
        for key in ('display_path','primary_path','raw_path','jpg_path','preview_path'):
            row[key]=Path(row[key]) if row[key] else None
        row['captured_at']=datetime.fromisoformat(row['captured_at'])
        if row['subject_features']:
            feature=row['subject_features']
            for key in ('face','head'):
                if feature.get(key):feature[key]=tuple(feature[key])
            row['subject_features']=SubjectFeatures(**feature)
        if row['preview_path'] and not row['preview_path'].is_file():raise ValueError('预览图丢失，请重新扫描')
        assets.append(PhotoAsset(**row))
    data['assets']=assets
    for key in ('preview_dir','contact_dir','workspace_dir','group_store_path','input_dir','screening_results_path'):
        data[key]=Path(data[key]) if data[key] else None
    for key in ('main_pages','rejected_pages'):
        data[key]=[Path(p) for p in data[key]] if data[key] is not None else None
    return ScanResult(**data)
