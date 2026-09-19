import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from ai_cull_assistant import updater as u


def installation(root,version,content=b'old',build=None):
    root.mkdir()
    (root/u.EXE).write_bytes(content)
    (root/'_internal').mkdir()
    (root/'_internal/lib.dll').write_bytes(content)
    data={'format':'photo-cull-program-v1','version':version,'files':{u.EXE:u.digest(root/u.EXE),'_internal/lib.dll':u.digest(root/'_internal/lib.dll')}}
    if build is not None:
        data['build']=build
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
    assert u.release_key('1.5.0',2)>u.release_key('1.5.0',1)
    assert u.release_key('1.5.1',0)>u.release_key('1.5.0',999)
    for value in ['../settings.json','_internal/../settings.json','settings.json','contact_sheets/a.jpg','C:/a','_internal/a:stream','_internal//a','_internal/a.']:
        assert not u.managed_name(value)
    with pytest.raises(ValueError): u.version_tuple('v0.4.10-beta')
    for build in [-1,True,'2',2.5,2_147_483_648]:
        with pytest.raises(ValueError): u.build_number(build)


def test_same_version_higher_build_installs_and_legacy_manifest_is_build_zero(tmp_path):
    old=tmp_path/'old'; new=tmp_path/'new'
    installation(old,'1.5.0')
    installation(new,'1.5.0',b'new',build=2)
    assert u.read_manifest(old)['build']==0
    assert u.validate_install(new,old)['build']==2
    installation(tmp_path/'older-build','1.5.0',build=1)
    with pytest.raises(ValueError,match='构建号'):
        u.validate_install(tmp_path/'older-build',new)


def test_build_manifest_uses_application_version_and_build(tmp_path):
    root=tmp_path/'portable'; root.mkdir()
    (root/u.EXE).write_bytes(b'exe')
    result=subprocess.run(
        [sys.executable,str(Path(__file__).parents[1]/'build_manifest.py'),str(root)],
        capture_output=True,text=True,check=False,
    )
    assert result.returncode==0,result.stderr
    manifest=json.loads((root/u.MANIFEST).read_text('utf-8'))
    assert (manifest['version'],manifest['build'])==(u.VERSION,u.BUILD)


def test_check_release_validates_asset(monkeypatch):
    import io
    tag='v99.0.0'
    package_url=f'https://github.com/{u.REPO}/releases/download/{tag}/AI-Photo-Cull-{tag}-Windows-x64-portable.zip'
    metadata_url=f'https://github.com/{u.REPO}/releases/download/{tag}/update.json'
    info=dict(version=tag,build=7,url=package_url,sha256='a'*64,size=12)
    metadata_payload=json.dumps(info).encode()
    data={'tag_name':tag,'assets':[
        {'name':package_url.rsplit('/',1)[-1],'state':'uploaded','digest':'sha256:'+'a'*64,'size':12,'browser_download_url':package_url},
        {'name':'update.json','state':'uploaded','size':len(metadata_payload),'browser_download_url':metadata_url},
    ]}
    monkeypatch.setattr(u,'request',lambda url:io.BytesIO(json.dumps(data).encode() if 'api.github.com' in url else metadata_payload))
    assert u.latest_release()==info
    info['sha256']='b'*64
    metadata_payload=json.dumps(info).encode()
    data['assets'][1]['size']=len(metadata_payload)
    with pytest.raises(ValueError,match='不匹配'):u.latest_release()
    info['sha256']='a'*64
    metadata_payload=json.dumps(info).encode()
    data['assets'][1]['size']=len(metadata_payload)
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
    updates=[]
    helper,plan=u.prepare_update(release,old,lambda percent,phase:updates.append((percent,phase)))
    assert helper.exists()
    assert json.loads(plan.read_text('utf-8'))['target']==str(old.resolve())
    assert (old/u.EXE).read_bytes()==b'old'
    assert updates[0]==(0,'download')
    assert (80,'download') in updates
    assert updates[-1]==(100,'ready')
    assert {phase for _,phase in updates}>={'download','verify','extract','validate','prepare','ready'}
    assert [percent for percent,_ in updates]==sorted(percent for percent,_ in updates)
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
    normalized={**info,'build':0}
    assert u.latest_release(cache)==normalized
    assert len(calls)==2
    assert u.latest_release(cache)==normalized and len(calls)==2
    assert u.latest_release(cache,force=True)==normalized and len(calls)==4
    saved=json.loads(cache.read_text('utf-8'))
    assert saved['client_version']==u.VERSION and saved['client_build']==u.BUILD
    info['url']='https://example.com/unsafe.zip'
    with pytest.raises(ValueError):u.latest_release(cache,force=True)


def test_update_cache_expires(monkeypatch,tmp_path):
    cache=tmp_path/'cache.json'
    cache.write_text(json.dumps(dict(client_version=u.VERSION,client_build=u.BUILD,checked_at=0,release=None)),encoding='utf-8')
    calls=[]
    monkeypatch.setattr(u,'api_release',lambda:calls.append(True))
    assert u.latest_release(cache) is None and calls==[True]
    assert u.latest_release(cache) is None and calls==[True]

@pytest.mark.parametrize('tag', ['v1.5', '1.5', 'v1.5.0', '1.5.0'])
def test_short_release_version_matches_full_version(tag):
    assert u.version_tuple(tag) == (1, 5, 0)


@pytest.mark.parametrize('tag', ['v1', 'v1.5-beta', 'v1.5/../x', '', None])
def test_invalid_release_versions_still_rejected(tag):
    with pytest.raises(ValueError):
        u.version_tuple(tag)


def test_short_tag_preserves_download_url_and_version_comparison(monkeypatch):
    import io
    tag = 'v1.5'
    url = f'https://github.com/{u.REPO}/releases/download/{tag}/AI-Photo-Cull-{tag}-Windows-x64-portable.zip'
    info = dict(version=tag, build=0, url=url, sha256='a'*64, size=20)
    metadata_url=f'https://github.com/{u.REPO}/releases/download/{tag}/update.json'
    metadata_payload=json.dumps(info).encode()
    data = dict(tag_name=tag, assets=[
        dict(name=url.rsplit('/',1)[-1], state='uploaded', digest='sha256:'+'a'*64, size=20, browser_download_url=url),
        dict(name='update.json', state='uploaded', size=len(metadata_payload), browser_download_url=metadata_url),
    ])
    monkeypatch.setattr(u, 'request', lambda target: io.BytesIO(json.dumps(data).encode() if 'api.github.com' in target else metadata_payload))
    monkeypatch.setattr(u, 'VERSION', '1.4.0')
    assert u.api_release() == info
    assert u.checked_release(info) == info
    monkeypatch.setattr(u, 'VERSION', '1.5.0')
    assert u.api_release() is None
    assert u.checked_release(info) is None


def test_same_version_release_requires_higher_build(monkeypatch):
    tag='v1.5'
    url=f'https://github.com/{u.REPO}/releases/download/{tag}/AI-Photo-Cull-{tag}-Windows-x64-portable.zip'
    monkeypatch.setattr(u,'VERSION','1.5.0')
    monkeypatch.setattr(u,'BUILD',4)
    base=dict(version=tag,url=url,sha256='a'*64,size=20)
    assert u.checked_release({**base,'build':5})['build']==5
    assert u.checked_release({**base,'build':4}) is None
    assert u.checked_release({**base,'build':3}) is None
    assert u.checked_release(base) is None


def test_api_discovers_same_version_repair_build_before_deciding(monkeypatch):
    import io
    tag='v1.5'
    package_url=f'https://github.com/{u.REPO}/releases/download/{tag}/AI-Photo-Cull-{tag}-Windows-x64-portable.zip'
    metadata_url=f'https://github.com/{u.REPO}/releases/download/{tag}/update.json'
    info=dict(version=tag,build=2,url=package_url,sha256='a'*64,size=20)
    data=dict(tag_name=tag,assets=[
        dict(name=package_url.rsplit('/',1)[-1],state='uploaded',digest='sha256:'+'a'*64,size=20,browser_download_url=package_url),
        dict(name='update.json',state='uploaded',size=0,browser_download_url=metadata_url),
    ])
    monkeypatch.setattr(u,'VERSION','1.5.0')
    monkeypatch.setattr(u,'BUILD',1)

    payload=[b'']
    def request(url):
        return io.BytesIO(json.dumps(data).encode() if 'api.github.com' in url else payload[0])
    monkeypatch.setattr(u,'request',request)

    payload[0]=json.dumps(info).encode()
    data['assets'][1]['size']=len(payload[0])
    assert u.api_release()==info

    info.pop('build')
    payload[0]=json.dumps(info).encode()
    data['assets'][1]['size']=len(payload[0])
    assert u.api_release() is None
