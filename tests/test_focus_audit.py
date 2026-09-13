import json
import zipfile
from pathlib import Path
from PIL import Image
import pytest
from ai_cull_assistant import ai_focus
from ai_cull_assistant.focus_audit import record_inputs, record_response, audit_root


def test_audit_deduplicates_actual_payload_and_excludes_secret(tmp_path):
    image=tmp_path/'eye.png'
    Image.new('RGB',(25,25),'red').save(image)
    cache=tmp_path/'work'/'cache'/'analysis'
    profile=dict(model='test',base_url='https://example.test',key='SECRET',api_key='SECRET')
    for digest in ('request1','request2'):
        record_inputs(cache,digest,[image], 'prompt',profile,'v2')
    root=audit_root(cache)
    assert root == tmp_path/'work'/'focus-evidence'
    blobs=list((root/'blobs').glob('*.zip'))
    assert len(blobs)==1
    with zipfile.ZipFile(blobs[0]) as z:
        assert z.read('image.png')==image.read_bytes()
    record_response(cache,'request2',dict(status='uncertain'))
    documents=[p.read_text('utf-8') for p in (root/'requests').glob('*.json')]
    assert all('SECRET' not in d for d in documents)
    assert any('uncertain' in d for d in documents)


@pytest.mark.parametrize('status,defocus,motion,expected',[
    ('clear','absent','present','uncertain'),
    ('blur','uncertain','absent','uncertain'),
    ('clear','absent','absent','clear'),
    ('blur','present','absent','blur'),
])
def test_structured_evidence_conflicts_cannot_silently_clear_or_reject(status,defocus,motion,expected):
    value=dict(photo_identity='test',status=status,reason='具体证据',
               observations=dict(defocus=defocus,motion_blur=motion,noise='present',occlusion='absent'))
    assert ai_focus._parse_response(json.dumps(value),'test')['status']==expected


def test_eye_crops_keep_exact_source_pixels(tmp_path,monkeypatch):
    import numpy as np
    from types import SimpleNamespace
    from ai_cull_assistant.crop_settings import CropSettings
    pixels=np.random.default_rng(3).integers(0,256,(400,400,3),dtype=np.uint8)
    monkeypatch.setattr(ai_focus,'load_full_image',lambda a:Image.fromarray(pixels))
    monkeypatch.setattr(ai_focus,'detail_features',lambda *a:SimpleNamespace(
        face=(.1,.1,.8,.8),landmarks=((.3,.4),(.7,.4))))
    images=ai_focus.prepare_focus_images(SimpleNamespace(preview_path=None),CropSettings(),tmp_path)
    eye=next(p for p in images if p.name=='eye_native_01.png')
    assert np.array_equal(np.asarray(Image.open(eye)),pixels[96:224,56:184])


def test_body_prompt_schema_and_conflicting_observations():
    prompt = ai_focus._prompt('photo', True, 2)
    assert '最后 2 张' in prompt
    schema = json.loads(next(line for line in prompt.splitlines() if line.startswith('{')))
    assert 'body_motion' in schema['observations']
    schema.update(status='clear', reason='脸清楚')
    schema['observations'] = dict(defocus='absent', motion_blur='absent', noise='absent',
                                  occlusion='absent', body_motion='present')
    assert ai_focus._parse_response(json.dumps(schema), 'photo')['status'] == 'uncertain'
