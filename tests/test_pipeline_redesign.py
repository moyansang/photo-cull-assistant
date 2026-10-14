from threading import Event
from PIL import Image
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.processing_job import start_job
from ai_cull_assistant.session_store import load_session


def test_unreadable_file_preserved_review_and_resume_safe(tmp_path):
    source = tmp_path / 'photos'; source.mkdir()
    (source / 'bad.jpg').write_bytes(b'not an image')
    Image.new('RGB', (100, 100), 'blue').save(source / 'ok.jpg')
    workspace = tmp_path / 'workspace'
    options = {'technical_screening': True, 'analysis_mode': 'landscape'}
    result = start_job(source, workspace, options, CropSettings(), mode='scan').run(options, CropSettings(), Event(), None)
    assert len(result.assets) == 2
    bad = next(a for a in result.assets if a.stem == 'bad')
    assert not bad.auto_rejected and bad.local_recommendation == 'review'
    assert bad.screening_reason == 'decode_unavailable'
    assert bad.preview_path.is_file()
    restored = load_session(workspace, source)
    assert len(restored.assets) == 2
    assert all(a.analysis_mode == 'landscape' for a in restored.assets)


def test_landscape_scan_has_no_subject_boxes_or_local_stars(tmp_path):
    source = tmp_path / 'photos'; source.mkdir()
    Image.new('RGB', (180, 120), 'skyblue').save(source / 'sky.jpg')
    options = {'technical_screening': True, 'analysis_mode': 'landscape'}
    result = start_job(source, tmp_path / 'workspace', options, CropSettings(), mode='scan').run(options, CropSettings(), Event(), None)
    a = result.assets[0]
    assert a.clarity_evidence['visible_boxes'] == []
    assert a.local_recommendation == 'review'
    assert not a.auto_rejected
    assert 'rating' not in a.clarity_evidence


def test_added_photo_joins_existing_automatic_group_across_directory(tmp_path):
    import shutil
    source = tmp_path / 'photos'; source.mkdir()
    Image.new('RGB', (180, 120), 'orange').save(source / 'one.jpg')
    options = {'technical_screening': False, 'analysis_mode': 'landscape'}
    work = tmp_path / 'work'
    initial = start_job(source, work, options, CropSettings(), mode='scan').run(options, CropSettings(), Event(), None)
    old_group = initial.assets[0].group_id
    (source / 'other').mkdir()
    shutil.copy2(source / 'one.jpg', source / 'other' / 'two.jpg')
    updated = start_job(source, work, options, CropSettings(), result=initial, mode='scan').run(options, CropSettings(), Event(), None)
    assert len(updated.assets) == 2
    assert {a.group_id for a in updated.assets} == {old_group}


def test_added_photo_does_not_join_protected_manual_group(tmp_path):
    import shutil
    from ai_cull_assistant.group_store import save_groups
    source = tmp_path / 'photos'; source.mkdir()
    Image.new('RGB', (180, 120), 'orange').save(source / 'one.jpg')
    options = {'technical_screening': False}
    work = tmp_path / 'work'
    initial = start_job(source, work, options, CropSettings(), mode='scan').run(options, CropSettings(), Event(), None)
    save_groups(initial.assets, work / 'groups.json', source='manual', collection_key=str(source))
    old_group = initial.assets[0].group_id
    shutil.copy2(source / 'one.jpg', source / 'two.jpg')
    updated = start_job(source, work, options, CropSettings(), result=initial, mode='scan').run(options, CropSettings(), Event(), None)
    assert next(a for a in updated.assets if a.stem == 'one').group_id == old_group
    assert next(a for a in updated.assets if a.stem == 'two').group_id != old_group


def test_recovered_decoder_replaces_placeholder_and_clears_failure_note(tmp_path):
    from ai_cull_assistant.grouping import visual_features
    from ai_cull_assistant.processing_job import _has_reviewable_subject
    source = tmp_path / 'photos'; source.mkdir()
    path = source / 'recover.jpg'; path.write_bytes(b'initial unreadable source')
    options = {'technical_screening': True, 'analysis_mode': 'landscape'}
    work = tmp_path / 'work'
    first = start_job(source, work, options, CropSettings(), mode='scan').run(options, CropSettings(), Event(), None)
    assert first.assets[0].preview_path.parent.name == 'unreadable'
    # Simulate a source repaired by its owner; a new explicit stage inventories
    # the new bytes. Opening an old session alone still rejects changed files.
    Image.new('RGB', (600, 400), 'orange').save(path)
    first.assets[0].ai_focus_dirty = True
    fixed = start_job(source, work, options, CropSettings(), result=first, mode='rescan').run(options, CropSettings(), Event(), None)
    a = fixed.assets[0]
    assert a.preview_path.is_file() and 'unreadable' not in a.preview_path.parts
    assert not any(n.startswith('decode_error:') for n in a.notes)
    assert visual_features(a) is not None
    assert _has_reviewable_subject(a, CropSettings())
    assert len(load_session(work, source).assets) == 1


def test_resume_cannot_silently_mix_analysis_modes(tmp_path):
    source = tmp_path / 'photos'; source.mkdir()
    for i in range(3):
        Image.new('RGB', (100, 100), 'gray').save(source / f'{i}.jpg')
    options = {'technical_screening': False, 'analysis_mode': 'landscape'}
    job = start_job(source, tmp_path / 'work', options, CropSettings(), mode='scan')
    stop = Event()
    assert job.run(options, CropSettings(), stop, lambda _: stop.set()) is None
    result = job.run(dict(options, analysis_mode='portrait'), CropSettings(), Event(), None)
    assert {a.analysis_mode for a in result.assets} == {'landscape'}
