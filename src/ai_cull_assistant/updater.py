"""Verified, manifest-only portable updates. User files are never update targets."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
import time
import zipfile

from .version import VERSION

REPO = 'moyansang/photo-cull-assistant'
MANIFEST = 'program-manifest.json'
EXE = 'AI选片助手.exe'


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('不支持的版本号')
    return tuple(map(int, match.groups()))


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'PhotoCullAssistant/'+VERSION}), timeout=25)


def api_release():
    with request(f'https://api.github.com/repos/{REPO}/releases/latest') as response:
        data = json.load(response)
    if data.get('draft') or data.get('prerelease') or version_tuple(data['tag_name']) <= version_tuple(VERSION):
        return None
    name = f"AI-Photo-Cull-{data['tag_name']}-Windows-x64-portable.zip"
    asset = next((a for a in data['assets'] if a['name']==name and a.get('state')=='uploaded'), None)
    if not asset or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', asset.get('digest') or ''):
        raise ValueError('新版尚无完整安装包或校验信息，请稍后再试')
    expected_url = f"https://github.com/{REPO}/releases/download/{data['tag_name']}/{name}"
    if asset['browser_download_url'] != expected_url:
        raise ValueError('更新下载地址不匹配')
    return dict(version=data['tag_name'], url=expected_url, sha256=asset['digest'][7:].lower(), size=asset['size'])


def checked_release(info):
    if info is None: return None
    version_tuple(info['version'])
    name=f"AI-Photo-Cull-{info['version']}-Windows-x64-portable.zip"
    expected=f"https://github.com/{REPO}/releases/download/{info['version']}/{name}"
    if info.get('url')!=expected or not re.fullmatch('[0-9a-f]{64}',info.get('sha256','')) or type(info.get('size')) is not int or not 0<info['size']<=1024*1024*1024:
        raise ValueError('更新信息校验失败')
    return info if version_tuple(info['version'])>version_tuple(VERSION) else None


def latest_release(cache_path=None, force=False):
    if cache_path and not force:
        try:
            cache=json.loads(Path(cache_path).read_text('utf-8'))
            if cache['client_version']==VERSION and 0<=time.time()-cache['checked_at']<21600:
                return checked_release(cache['release'])
        except (OSError,ValueError,KeyError,TypeError): pass
    try:
        release=api_release()
    except (urllib.error.URLError,TimeoutError):
        # Public release assets use the GitHub web/CDN route, not REST quota.
        with request(f'https://github.com/{REPO}/releases/latest/download/update.json') as response:
            release=checked_release(json.load(response))
    if cache_path:
        try:
            path=Path(cache_path)
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(client_version=VERSION,checked_at=time.time(),release=release)),encoding='utf-8')
            temporary.replace(path)
        except OSError: pass
    return release


def digest(path):
    with open(path,'rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def managed_name(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or any(v in ('','.','..') or v.endswith((' ','.')) for v in name.split('/')):
        return False
    return name in (EXE,'README-快速开始.txt') or p.parts[0] in ('_internal','lightroom')


def read_manifest(root):
    data = json.loads((root/MANIFEST).read_text('utf-8'))
    if data.get('format')!='photo-cull-program-v1' or not isinstance(data.get('files'),dict):
        raise ValueError('程序文件清单无效')
    version_tuple(data['version'])
    keys=set()
    for name, value in data['files'].items():
        if not managed_name(name) or name.casefold() in keys or not re.fullmatch('[0-9a-f]{64}',value):
            raise ValueError('程序清单包含无效路径或校验值')
        keys.add(name.casefold())
    if EXE not in data['files']:
        raise ValueError('更新包缺少 EXE')
    return data


def safe_target(root, name):
    root=root.resolve()
    target=root/name
    if not target.resolve().is_relative_to(root):
        raise ValueError('更新目标越出安装目录')
    for part in [target,*target.parents]:
        if part==root: break
        if part.exists() and part.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('更新路径包含链接，已停止')
    return target


def validate_install(stage, target):
    new=read_manifest(stage); old=read_manifest(target)
    if version_tuple(new['version'])<=version_tuple(old['version']):
        raise ValueError('更新版本不高于已安装版本')
    for name, sha in new['files'].items():
        source=safe_target(stage,name); dest=safe_target(target,name)
        if not source.is_file() or digest(source)!=sha:
            raise ValueError('更新文件校验失败：'+name)
        if dest.exists() and (name not in old['files'] or not dest.is_file() or digest(dest)!=old['files'][name]):
            raise ValueError('检测到用户新增或修改的同名文件，停止更新以保留内容：'+name)
    safe_target(target,MANIFEST)
    return new


def prepare_update(release, target, progress=None):
    def report(percent, phase):
        if progress:
            progress(percent, phase)

    report(0, 'download')
    work=Path(tempfile.mkdtemp(prefix='photo-cull-update-'))
    package=work/'release.zip'
    with request(release['url']) as response, package.open('wb') as stream:
        total=0
        while chunk:=response.read(1024*1024):
            total+=len(chunk)
            if total>release['size'] or total>1024*1024*1024:
                raise ValueError('更新包大小异常')
            stream.write(chunk)
            report(min(80, total*80//release['size']), 'download')
    report(82, 'verify')
    if package.stat().st_size!=release['size'] or digest(package)!=release['sha256']:
        raise ValueError('下载不完整或 SHA256 校验失败，原程序未修改')
    stage=work/'stage'; stage.mkdir()
    report(85, 'extract')
    with zipfile.ZipFile(package) as archive:
        members={}
        expanded=0
        for entry in archive.infolist():
            if entry.is_dir(): continue
            p=PurePosixPath(entry.filename)
            if len(p.parts)<2 or p.parts[0]!='AI选片助手':
                raise ValueError('安装包目录结构不正确')
            name='/'.join(p.parts[1:])
            if (name!=MANIFEST and not managed_name(name)) or name.casefold() in members or stat.S_ISLNK(entry.external_attr>>16):
                raise ValueError('安装包包含非程序文件或不安全路径')
            members[name.casefold()]=(name,entry)
            expanded+=entry.file_size
            if expanded>2*1024*1024*1024: raise ValueError('解压大小异常')
        for index, (name,entry) in enumerate(members.values(), 1):
            destination=stage/name; destination.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(entry) as source, destination.open('wb') as out:
                shutil.copyfileobj(source,out)
            report(85 + index*10//len(members), 'extract')
    report(96, 'validate')
    new=validate_install(stage,target)
    if version_tuple(new['version'])!=version_tuple(release['version']): raise ValueError('安装包版本不匹配')
    if set(members)!={n.casefold() for n in new['files']}|{MANIFEST.casefold()}: raise ValueError('安装包文件与清单不一致')
    report(98, 'prepare')
    helper=work/'helper'; helper.mkdir()
    shutil.copy2(target/EXE,helper/EXE)
    shutil.copytree(target/'_internal',helper/'_internal')
    plan=dict(target=str(target.resolve()),stage=str(stage),backup=str(work/'backup'),parent=os.getpid())
    path=work/'plan.json'; path.write_text(json.dumps(plan),encoding='utf-8')
    report(100, 'ready')
    return helper/EXE,path


def install_files(stage,target,backup):
    new=validate_install(stage,target)
    backup.mkdir(parents=True,exist_ok=False)
    names=[*new['files'],MANIFEST]
    existed=[]; created=[]
    # Finish all backups before making the first program change.
    for name in names:
        dest=safe_target(target,name)
        if dest.exists():
            saved=backup/name; saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(dest,saved); existed.append(name)
        else: created.append(name)
    try:
        for name in names:
            dest=safe_target(target,name); dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(stage/name,dest)
    except Exception:
        for name in existed: shutil.copy2(backup/name,safe_target(target,name))
        for name in created:
            dest=safe_target(target,name)
            if dest.is_file(): dest.unlink()
        raise


def run_installer(plan_path):
    import ctypes
    plan=json.loads(Path(plan_path).read_text('utf-8'))
    target=Path(plan['target'])
    try:
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x100000,False,int(plan['parent']))
        if handle:
            try:
                if kernel.WaitForSingleObject(handle,60000)!=0: raise RuntimeError('原程序未退出，取消更新')
            finally: kernel.CloseHandle(handle)
        install_files(Path(plan['stage']),target,Path(plan['backup']))
    except Exception as exc:
        (Path(plan_path).parent/'error.txt').write_text(str(exc),encoding='utf-8')
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw()
        messagebox.showerror('更新未完成',f'{exc}\n更新备份与日志：{Path(plan_path).parent}')
        root.destroy()
    subprocess.Popen([str(target/EXE)],cwd=str(target))
