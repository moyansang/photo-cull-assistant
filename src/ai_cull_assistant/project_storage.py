"""User data roots, per-source workspace registry and non-destructive migration."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid
from .settings import application_dir, read_values, save_values

WORKSPACE_NAMES = (
    'scan-session.json', 'ai_project.json', 'groups.json', 'screening_results.json',
    'processing-settings.json', 'workspace-settings.json', 'lightroom_results.json',
    'session.log', 'previews', 'contact_sheets', 'ai_tasks', '.analysis-cache', '.processing',
    'exports', 'logs', 'cache',
)

def _atomic(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

def _key(path):
    return str(Path(path).resolve()).casefold()

def runtime_data_root():
    if getattr(sys,'frozen',False):return application_dir()
    repo=application_dir()
    base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))
    identity=hashlib.sha256(_key(repo).encode()).hexdigest()[:12]
    return base/'AIPhotoCullAssistant'/'development'/identity

def _default_workspace(settings_dir,input_dir):
    base=Path(settings_dir)
    root=base.parent if base.name=='settings' else base
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',Path(input_dir).name).strip(' .') or '照片'
    suffix=hashlib.sha256(_key(input_dir).encode()).hexdigest()[:10]
    return root/'workspaces'/f'{name}-{suffix}'

def workspace_for(settings_dir,input_dir,preferred=None):
    if not str(input_dir).strip():
        return _default_workspace(settings_dir,'未选择照片')
    settings_dir=Path(settings_dir)
    values=read_values(settings_dir)
    registry=values.get('workspaces',{})
    if not isinstance(registry,dict):registry={}
    source=_key(input_dir)
    chosen=Path(preferred or registry.get(source) or _default_workspace(settings_dir,input_dir)).resolve()
    for other,target in registry.items():
        if other!=source and _key(target)==_key(chosen):
            raise ValueError('这个工作区已关联其他照片文件夹，请选择独立工作区。')
    from .workspace_archive import restore_workspace
    restore_workspace(chosen)
    session=chosen/'scan-session.json'
    if session.is_file():
        data=json.loads(session.read_text(encoding='utf-8'))
        previous=data.get('input_dir')
        if previous and _key(previous)!=source:
            raise ValueError('这个工作区包含另一批照片的分析，请选择独立工作区。')
    chosen.mkdir(parents=True,exist_ok=True)
    registry[source]=str(chosen)
    save_values(settings_dir,{'workspaces':registry})
    return chosen

def load_workspace_preferences(settings_dir,workspace,input_dir):
    from .workspace_archive import restore_workspace
    restore_workspace(workspace)
    file=Path(workspace)/'workspace-settings.json'
    if file.exists():
        data=json.loads(file.read_text(encoding='utf-8'))
        if data.get('input_dir') and _key(data['input_dir'])!=_key(input_dir):
            raise ValueError('工作区设置属于其他照片文件夹。')
        return data
    legacy=read_values(Path(settings_dir))
    # Only the legacy last-opened source inherits its per-photo overrides.
    same=bool(legacy.get('input')) and _key(legacy['input'])==_key(input_dir)
    return {'face_crop':legacy.get('face_crop',{}) if same else {},
            'options':legacy.get('options',{}) if same else {}}

def save_workspace_preferences(workspace,face_crop,options):
    path=Path(workspace)/'workspace-settings.json'
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    options={k:v for k,v in options.items() if k!='no_auto_updates'}
    _atomic(path,dict(previous,version=1,face_crop=face_crop,options=options))

def migrate_workspace(source,destination):
    """Copy only application-owned data, rebase references, keep originals intact."""
    source=Path(source).resolve();destination=Path(destination).resolve()
    if source==destination:return destination
    marker=destination/'migration.json'
    if marker.exists():
        record=json.loads(marker.read_text(encoding='utf-8'))
        if record.get('source')==str(source):return destination
    if destination.exists():
        raise ValueError('迁移目标已存在，保留两处数据，未覆盖：'+str(destination))
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=destination.with_name(destination.name+'.migration-'+uuid.uuid4().hex)
    stage.mkdir()
    mappings=[(source/name,destination/name) for name in WORKSPACE_NAMES]
    def relocated(value,field=None):
        if field in {'input_dir','primary_path','raw_path','jpg_path','display_path','target_paths','source_stats','path'}:
            return value
        if isinstance(value,dict):return {relocated(k):relocated(v,k) for k,v in value.items()}
        if isinstance(value,list):return [relocated(v,field) for v in value]
        if not isinstance(value,str):return value
        candidate=Path(value)
        if not candidate.is_absolute():return value
        if field in {'workspace','workspace_dir'} and _key(candidate)==_key(source):return str(destination)
        for old,new in mappings:
            try:return str(new/candidate.relative_to(old))
            except ValueError:pass
        return value
    try:
        for name in WORKSPACE_NAMES:
            old=source/name;new=stage/name
            if not old.exists():continue
            if old.is_symlink() or getattr(old,'is_junction',lambda:False)():raise ValueError('工作区数据包含符号链接，未自动迁移：'+str(old))
            if old.is_dir():
                # Do not follow directory links outside the selected workspace.
                if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in old.rglob('*')):
                    raise ValueError('工作区数据包含链接，未自动迁移：'+str(old))
                shutil.copytree(old,new)
            else:shutil.copy2(old,new)
        for file in stage.rglob('*.json'):
            value=json.loads(file.read_text(encoding='utf-8-sig'))
            transformed=relocated(value)
            if transformed!=value:_atomic(file,transformed)
        _atomic(stage/'migration.json',{'version':1,'source':str(source),'originals_retained':True})
        stage.rename(destination)
    except Exception:
        # Keep the failed staging copy for inspection, never remove source data.
        raise
    return destination

def prepare_runtime_settings():
    program=application_dir();root=runtime_data_root();target=root/'settings'
    target.mkdir(parents=True,exist_ok=True)
    marker=target/'migration.json'
    if marker.exists():return target
    for name in ('settings.json','ai-api-profiles.json','update-cache.json'):
        old=program/name;new=target/name
        if old.is_file() and not new.exists():shutil.copy2(old,new)
    values=read_values(target)
    source=values.get('input','')
    old_workspace=Path(values.get('workspace') or program).resolve()
    if source:
        preferred=old_workspace
        if old_workspace==program.resolve():
            preferred=_default_workspace(target,source)
            if any((program/name).exists() for name in WORKSPACE_NAMES):
                migrate_workspace(program,preferred)
        chosen=workspace_for(target,source,preferred=preferred)
        save_values(target,{'input':source,'workspace':str(chosen)})
        prefs=load_workspace_preferences(target,chosen,source)
        save_workspace_preferences(chosen,prefs.get('face_crop',{}),prefs.get('options',{}))
        migrated=read_values(target)
        migrated.pop('face_crop',None)
        old_options=migrated.get('options',{})
        migrated['options']={'no_auto_updates':old_options.get('no_auto_updates',False)} if isinstance(old_options,dict) else {}
        _atomic(target/'settings.json',migrated)
    else:
        save_values(target,{'workspace':''})
    _atomic(marker,{'version':1,'source':str(program),'originals_retained':True})
    return target
