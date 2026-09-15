from types import SimpleNamespace
from PIL import Image
import pytest
from ai_cull_assistant.shared_decode import full_image, shared_decode


def test_lazy_single_decode_and_release_on_failure(monkeypatch):
    calls=[]
    def decode(asset):
        image=Image.new('RGB',(20,20));calls.append(image);return image
    monkeypatch.setattr('ai_cull_assistant.face_focus.load_full_image',decode)
    a=object()
    with shared_decode(a):
        pass
    assert not calls
    with pytest.raises(RuntimeError), shared_decode(a):
        with full_image(a) as face: assert face.size==(20,20)
        with full_image(a) as body: assert body is face
        raise RuntimeError('body check failed')
    assert len(calls)==1
    with pytest.raises(ValueError):calls[0].getpixel((0,0))


def test_screening_shares_face_and_body_image(monkeypatch, tmp_path):
    from ai_cull_assistant.screening import screen_assets, ScreeningResult
    calls=[]
    def decode(asset):
        image=Image.new('RGB',(20,20));calls.append(image);return image
    monkeypatch.setattr('ai_cull_assistant.face_focus.load_full_image',decode)
    def face(asset,**kwargs):
        with full_image(asset) as image:assert image.size==(20,20)
        return ScreeningResult(False,'clear',True,focus_evidence={'state':'clear'})
    def body(asset,result,*args):
        with full_image(asset) as image:assert image is calls[-1]
        return result
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',face)
    monkeypatch.setattr('ai_cull_assistant.body_pipeline.apply_body_check',body)
    assets=[SimpleNamespace(preview_path=tmp_path/'x.jpg',stem=str(i)) for i in range(2)]
    screen_assets(assets,body_check=True)
    assert len(calls)==2
    for image in calls:
        with pytest.raises(ValueError):image.getpixel((0,0))
