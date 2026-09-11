from dataclasses import asdict
import pytest
from test_ai_project import setup_project
from ai_cull_assistant.workflow import ScanResult
from ai_cull_assistant.subject import SubjectFeatures
from ai_cull_assistant.session_store import save_session,load_session


def test_roundtrip_analysis_and_changed_original(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    assets[0].subject_features=SubjectFeatures('a','b',None,(.1,.2,.3,.4))
    assets[0].subject_checked=True
    result=ScanResult(assets,project.workspace/'previews',project.workspace/'contact_sheets',project.workspace,project.workspace/'groups.json',input_dir=tmp_path/'photos')
    save_session(result)
    restored=load_session(project.workspace,tmp_path/'photos')
    assert asdict(restored)==asdict(result)
    assert load_session(project.workspace,tmp_path/'other') is None
    before=(project.workspace/'scan-session.json').read_bytes()
    assets[0].primary_path.write_bytes(b'changed')
    with pytest.raises(ValueError):load_session(project.workspace,tmp_path/'photos')
    with pytest.raises(ValueError):save_session(restored)
    assert (project.workspace/'scan-session.json').read_bytes()==before


def test_app_reopens_analysis_and_logs(tmp_path,monkeypatch):
    from ai_cull_assistant.app import App
    from ai_cull_assistant.settings import save_values
    project,assets,task,batch=setup_project(tmp_path)
    save_values(tmp_path,dict(input=str(tmp_path/'photos'),workspace=str(project.workspace)))
    app=App(settings_dir=tmp_path);app.withdraw()
    app.scan_result=ScanResult(assets,project.workspace/'previews',project.workspace/'contact_sheets',project.workspace,project.workspace/'groups.json',input_dir=tmp_path/'photos')
    app._log('上次分析完成')
    app._close()
    monkeypatch.setattr('ai_cull_assistant.app.run_scan',lambda *a,**kw:pytest.fail('must not rescan'))
    reopened=App(settings_dir=tmp_path);reopened.withdraw();reopened.update()
    try:
        assert len(reopened.scan_result.assets)==2
        assert '上次分析完成' in reopened.log_text.get('1.0','end')
        assert project.path.exists()
    finally:reopened._close()
