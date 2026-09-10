import json
import pytest
from ai_cull_assistant import ai_api
from ai_cull_assistant.ai_presets import PRESETS, preset_profile, matching_preset
from test_ai_api import response


@pytest.mark.parametrize('preset_id',list(PRESETS))
def test_preset_request_uses_expected_endpoint_model_and_parameters(tmp_path,monkeypatch,preset_id):
    preset=PRESETS[preset_id]
    profile=ai_api.save_profile(tmp_path,preset_profile(preset_id))
    assert ai_api.load_profiles(tmp_path)[0]['preset_id']==preset_id
    image=tmp_path/'sample.jpg';image.write_bytes(b'test-image')
    monkeypatch.setattr(ai_api,'get_secret',lambda _: 'dummy-secret')
    calls=[]
    def send(request,timeout):
        calls.append(request)
        assert timeout==300
        return response('ok')
    monkeypatch.setattr(ai_api,'_open_request',send)
    ai_api.call_model(profile,'look',[image])
    body=json.loads(calls[0].data)
    assert calls[0].full_url==preset['base_url']+'/chat/completions'
    assert body['model']==preset['model']
    assert body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    for k,v in preset['request_options'].items():assert body[k]==v
    parameter=preset.get('token_parameter','max_tokens')
    assert body[parameter]==8192
    if parameter=='max_completion_tokens':assert 'max_tokens' not in body


def test_reuses_only_matching_provider_credentials(tmp_path,monkeypatch):
    secrets={}
    monkeypatch.setattr(ai_api,'_write_secret',lambda pid,key: secrets.update({pid:key}))
    monkeypatch.setattr(ai_api,'get_secret',lambda pid: secrets.get(pid))
    plus=ai_api.save_profile(tmp_path,preset_profile('qwen-vl-plus'),'qwen-secret')
    flash=ai_api.save_profile(tmp_path,preset_profile('qwen-vl-flash'))
    glm=ai_api.save_profile(tmp_path,preset_profile('glm-vision-flash'))
    assert secrets[flash['id']]==secrets[plus['id']]
    assert glm['id'] not in secrets
    assert 'qwen-secret' not in (tmp_path/ai_api.PROFILE_FILE).read_text('utf-8')


def test_endpoint_edit_requires_explicit_key_and_removes_preset(tmp_path,monkeypatch):
    monkeypatch.setattr(ai_api,'_write_secret',lambda *args:None)
    saved=ai_api.save_profile(tmp_path,preset_profile('qwen-vl-plus'),'dummy')
    changed={**saved,'base_url':'https://another.example/v1'}
    assert matching_preset(changed) is None
    with pytest.raises(ai_api.CredentialError,match='重新输入'):
        ai_api.save_profile(tmp_path,changed)
    new=ai_api.save_profile(tmp_path,changed,'new-service-key')
    assert 'preset_id' not in new
    assert 'preset_id' not in ai_api.load_profiles(tmp_path)[0]


def test_model_edit_keeps_custom_profile_without_preset_options(tmp_path):
    saved=ai_api.save_profile(tmp_path,{**preset_profile('openai-mini'),'model':'custom-model'})
    assert 'preset_id' not in saved


def test_deepseek_body_limit_applies_before_network(tmp_path,monkeypatch):
    profile=ai_api.save_profile(tmp_path,preset_profile('deepseek-vision'))
    monkeypatch.setattr(ai_api,'get_secret',lambda _: 'dummy')
    monkeypatch.setitem(PRESETS['deepseek-vision'],'max_request_bytes',120)
    monkeypatch.setattr(ai_api,'_open_request',lambda *args: pytest.fail('must not send oversized request'))
    with pytest.raises(ValueError,match='拆小'):
        ai_api.call_model(profile,'long prompt'*30,[])


def test_failed_endpoint_save_does_not_replace_old_service_secret(tmp_path,monkeypatch):
    secrets={}
    monkeypatch.setattr(ai_api,'_write_secret',lambda pid,key: secrets.update({pid:key}))
    saved=ai_api.save_profile(tmp_path,preset_profile('qwen-vl-plus'),'old-service-key')
    def cannot_write(*args):raise OSError('disk unavailable')
    monkeypatch.setattr(ai_api,'_write_profiles',cannot_write)
    with pytest.raises(OSError):
        ai_api.save_profile(tmp_path,{**saved,'base_url':'https://different.example/v1'},'new-service-key')
    original=ai_api.load_profiles(tmp_path)[0]
    assert original['base_url']==saved['base_url']
    assert secrets[original['id']]=='old-service-key'
