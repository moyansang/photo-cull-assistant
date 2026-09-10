import hashlib
import json
from pathlib import Path
import shutil
import pytest
from ai_cull_assistant import updater as u


def installation(root,version,content=b'old'):
    root.mkdir()
    (root/u.EXE).write_bytes(content)
    (root/'_internal').mkdir()
    (root/'_internal/lib.dll').write_bytes(content)
    data={'format':'photo-cull-program-v1','version':version,'files':{u.EXE:u.digest(root/u.EXE),'_internal/lib.dll':u.digest(root/'_internal/lib.dll')}}
    (root/u.MANIFEST).write_text(json.dumps(data),encoding='utf-8')
    return data


def test_install_preserves_all_user_content(tmp_path):
    old=tmp_path/'old'; new=tmp_path/'new'
    installation(old,'0.4.9'); installation(new,'0.4.10',b'new')
    for name in ['settings.json','groups.json','contact_sheets/a.jpg','previews/a.jpg','精选/a.RW2','_internal/user-note.txt']:
        p=old/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(b'user')
    u.install_files(new,old,tmp_path/'backup')
    assert (old/u.EXE).read_bytes()==b'new'
    for name in ['settings.json','groups.json','contact_sheets/a.jpg','previews/a.jpg','精选/a.RW2','_internal/user-note.txt']:
        assert (old/name).read_bytes()==b'user'
    assert (tmp_path/'backup'/u.EXE).read_bytes()==b'old'


def test_modified_program_and_unowned_collision_abort(tmp_path):
    old=tmp_path/'old'; new=tmp_path/'new'
    installation(old,'0.4.9');data=installation(new,'0.4.10',b'new')
    (old/u.EXE).write_bytes(b'user-modified')
    with pytest.raises(ValueError,match='用户'): u.validate_install(new,old)
    (old/u.EXE).write_bytes(b'old')
    for root in [old,new]: (root/'_internal/new.txt').write_bytes(b'user')
    data['files']['_internal/new.txt']=u.digest(new/'_internal/new.txt')
    (new/u.MANIFEST).write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='用户'): u.validate_install(new,old)


def test_copy_failure_restores_prior_install(tmp_path,monkeypatch):
    old=tmp_path/'old'; new=tmp_path/'new'
    installation(old,'0.4.9');installation(new,'0.4.10',b'new')
    saved={p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}
    original=shutil.copy2
    def copy(source,target,*args,**kwargs):
        if Path(source)==new/'_internal/lib.dll': raise OSError('simulated locked file')
        return original(source,target,*args,**kwargs)
    monkeypatch.setattr(u.shutil,'copy2',copy)
    with pytest.raises(OSError): u.install_files(new,old,tmp_path/'backup')
    assert saved=={p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}


def test_versions_and_protected_names():
    assert u.version_tuple('v0.4.10')>u.version_tuple('0.4.9')
    for value in ['../settings.json','_internal/../settings.json','settings.json','contact_sheets/a.jpg','C:/a','_internal/a:stream','_internal//a','_internal/a.']:
        assert not u.managed_name(value)
    with pytest.raises(ValueError): u.version_tuple('v0.4.10-beta')


def test_check_release_validates_asset(monkeypatch):
    import io
    data={'tag_name':'v99.0.0','assets':[{'name':'AI-Photo-Cull-v99.0.0-Windows-x64-portable.zip','state':'uploaded','digest':'sha256:'+'a'*64,'size':12,'browser_download_url':f'https://github.com/{u.REPO}/releases/download/v99.0.0/AI-Photo-Cull-v99.0.0-Windows-x64-portable.zip'}]}
    monkeypatch.setattr(u,'request',lambda url:io.BytesIO(json.dumps(data).encode()))
    assert u.latest_release()['version']=='v99.0.0'
    data['assets'][0]['browser_download_url']='https://example.com/package.zip'
    with pytest.raises(ValueError):u.latest_release()
    data['prerelease']=True
    assert u.latest_release() is None


def test_prepare_download_checksum_and_file_inventory(tmp_path,monkeypatch):
    import io,zipfile
    old=tmp_path/'old';new=tmp_path/'new'
    installation(old,'0.4.9');installation(new,'0.4.10',b'new')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for path in new.rglob('*'):
            if path.is_file():archive.write(path,'AI选片助手/'+path.relative_to(new).as_posix())
    payload=buffer.getvalue()
    release=dict(url='test',version='v0.4.10',size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(u,'request',lambda url:io.BytesIO(payload))
    staging=tmp_path/'staging';staging.mkdir()
    monkeypatch.setattr(u.tempfile,'mkdtemp',lambda **kw:str(staging))
    helper,plan=u.prepare_update(release,old)
    assert helper.exists()
    assert json.loads(plan.read_text('utf-8'))['target']==str(old.resolve())
    assert (old/u.EXE).read_bytes()==b'old'
    release['sha256']='0'*64
    with pytest.raises(ValueError,match='校验'):u.prepare_update(release,old)


def test_rate_limit_falls_back_and_caches(monkeypatch,tmp_path):
    import io,urllib.error
    info=dict(version='v99.0.0',url=f'https://github.com/{u.REPO}/releases/download/v99.0.0/AI-Photo-Cull-v99.0.0-Windows-x64-portable.zip',sha256='a'*64,size=20)
    calls=[]
    def request(url):
        calls.append(url)
        if 'api.github.com' in url:raise urllib.error.HTTPError(url,403,'rate limit exceeded',{},None)
        return io.BytesIO(json.dumps(info).encode())
    monkeypatch.setattr(u,'request',request)
    cache=tmp_path/'cache.json'
    assert u.latest_release(cache)==info
    assert len(calls)==2
    assert u.latest_release(cache)==info and len(calls)==2
    assert u.latest_release(cache,force=True)==info and len(calls)==4
    info['url']='https://example.com/unsafe.zip'
    with pytest.raises(ValueError):u.latest_release(cache,force=True)


def test_update_cache_expires(monkeypatch,tmp_path):
    cache=tmp_path/'cache.json'
    cache.write_text(json.dumps(dict(client_version=u.VERSION,checked_at=0,release=None)),encoding='utf-8')
    calls=[]
    monkeypatch.setattr(u,'api_release',lambda:calls.append(True))
    assert u.latest_release(cache) is None and calls==[True]
    assert u.latest_release(cache) is None and calls==[True]
