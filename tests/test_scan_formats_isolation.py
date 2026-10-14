from pathlib import Path
from PIL import Image
from ai_cull_assistant.scanner import scan_folder
from ai_cull_assistant.ai_project import photo_id
from ai_cull_assistant.models import IMAGE_EXTENSIONS


def test_pairing_never_crosses_subdirectories(tmp_path):
    for folder in ('one', 'two'):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / 'same.RW2').write_bytes(b'raw fixture')
        Image.new('RGB', (20, 20)).save(tmp_path / folder / 'same.JPG')
    assets = scan_folder(tmp_path)
    assert len(assets) == 2
    assert len({a.stem for a in assets}) == 2
    assert len({photo_id(a) for a in assets}) == 2
    assert all(a.raw_path.parent == a.jpg_path.parent for a in assets)


def test_same_stem_distinct_nonpair_formats_are_not_dropped(tmp_path):
    for ext in ('jpg', 'png', 'bmp', 'tiff'):
        Image.new('RGB', (20, 20)).save(tmp_path / ('same.' + ext))
    assets = scan_folder(tmp_path)
    assert len(assets) == 4
    assert len({a.stem for a in assets}) == 4


def test_new_formats_discovered_but_not_claimed_decodable(tmp_path):
    for ext in ('heic', 'heif', 'avif'):
        (tmp_path / ('sample.' + ext)).write_bytes(b'invalid fixture')
    assets = scan_folder(tmp_path)
    assert len(assets) == 3
    assert {'.bmp', '.heic', '.heif', '.avif'} <= IMAGE_EXTENSIONS


def test_ordinary_stem_and_path_identity_unchanged(tmp_path):
    path = tmp_path / 'photo.jpg'
    Image.new('RGB', (20, 20)).save(path)
    asset = scan_folder(tmp_path)[0]
    assert asset.stem == 'photo'
    assert asset.primary_path == path
