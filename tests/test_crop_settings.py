from dataclasses import asdict
from datetime import datetime

from PIL import Image

from ai_cull_assistant.crop_settings import CropSettings, crop_bounds
from ai_cull_assistant.settings import save_paths, save_values, read_values, load_paths
from ai_cull_assistant import contact_sheet
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.subject import SubjectFeatures


def test_scale_shift_ratio_and_bounds():
    head = (.3, .3, .2, .3)
    normal = crop_bounds((1000, 1000), head, CropSettings())
    bigger = crop_bounds((1000, 1000), head, CropSettings(1.5))
    up = crop_bounds((1000, 1000), head, CropSettings(1, -.2))
    assert bigger[2]-bigger[0] > normal[2]-normal[0]
    assert up[1] < normal[1]
    square = crop_bounds((1000, 1000), head, CropSettings(aspect_ratio='1:1'))
    assert square[2]-square[0] == square[3]-square[1]
    edges = crop_bounds((100, 100), (0, 0, .7, .9), CropSettings(2, -.5))
    assert 0 <= edges[0] < edges[2] <= 100 and 0 <= edges[1] < edges[3] <= 100


def test_directory_and_crop_saves_preserve_each_other(tmp_path):
    settings = CropSettings(1.2, -.1, '3:4')
    save_values(tmp_path, {'face_crop': asdict(settings)})
    save_paths(tmp_path, {'input': 'D:/photos', 'workspace': 'D:/work', 'export': 'D:/out'})
    assert CropSettings.from_dict(read_values(tmp_path)['face_crop']) == settings
    save_values(tmp_path, {'face_crop': asdict(CropSettings())})
    assert load_paths(tmp_path)['input'] == 'D:/photos'
    assert CropSettings.from_dict({'scale_factor': float('nan')}) == CropSettings()


def test_regenerate_reuses_detection_and_does_not_change_groups(tmp_path, monkeypatch):
    path = tmp_path / 'photo.jpg'
    Image.new('RGB', (500, 800), 'blue').save(path)
    asset = PhotoAsset('TEST', path, path, None, path, datetime.now(), '.jpg', group_id=7, preview_path=path)
    calls = []
    def detect(p):
        calls.append(p)
        return SubjectFeatures('0', '0', '0', (.3,.2,.2,.2), (.2,.1,.4,.4))
    monkeypatch.setattr(contact_sheet, 'features', detect)
    contact_sheet.generate_contact_sheet_sets([asset], tmp_path/'sheets')
    contact_sheet.generate_contact_sheet_sets([asset], tmp_path/'sheets', crop_settings=CropSettings(1.5, -.1, '1:1'))
    assert len(calls) == 1
    assert asset.group_id == 7
    assert not path.with_suffix('.xmp').exists()
