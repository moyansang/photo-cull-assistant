import json
import sys
from types import SimpleNamespace
import pytest
from ai_cull_assistant.af_inspect import main


def fake_reader(monkeypatch,status='not_validated'):
    calls=[]
    def read(path,**kwargs):
        calls.append((path,kwargs));return {'status':status,'source':str(path)}
    monkeypatch.setitem(sys.modules,'ai_cull_assistant.af_metadata',SimpleNamespace(read_af_metadata=read))
    return calls


def test_explicit_cli_keeps_unknown_metadata_and_no_decisions(monkeypatch,capsys,tmp_path):
    calls=fake_reader(monkeypatch);p=tmp_path/'照片 -测试.jpg'
    assert main([str(p)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['automatic_decisions_enabled'] is False
    assert result['records'][0]['status']=='not_validated' and calls[0][0]==p


def test_report_never_overwrites_input_or_existing_file(monkeypatch,tmp_path):
    calls=fake_reader(monkeypatch);p=tmp_path/'原图.jpg';p.write_bytes(b'original')
    for dest in (p,tmp_path/'existing.json'):
        if dest!=p:dest.write_text('existing')
        with pytest.raises(SystemExit):main([str(p),'--output',str(dest)])
    assert calls==[] and p.read_bytes()==b'original'


def test_new_report_and_parser_error_status(monkeypatch,tmp_path):
    fake_reader(monkeypatch,'error');out=tmp_path/'report.json'
    assert main([str(tmp_path/'照片.rw2'),'--output',str(out)])==2
    assert json.loads(out.read_text())['records'][0]['status']=='error'


@pytest.mark.parametrize('value',['nan','inf','0','-1','121'])
def test_cli_rejects_unbounded_timeout(value):
    with pytest.raises(SystemExit):main(['example.jpg','--timeout',value])
