from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from ai_cull_assistant import grouping
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.subject import SubjectFeatures


def assets(count, step=.2):
    return [PhotoAsset(str(i), Path(str(i)), Path(str(i)), None, None,
                       datetime(2026, 1, 1) + timedelta(seconds=i * step), '.jpg',
                       preview_path=Path(str(i))) for i in range(count)]


def visual(body, face=(.4, .1, .1, .1)):
    return SubjectFeatures('0', '0', body, face)


def setup_visuals(monkeypatch, items, values):
    base = grouping._visual_from_image(Image.new('L', (64, 64), 127))
    monkeypatch.setattr(grouping, 'visual_features', lambda asset: base)
    for asset, value in zip(items, values):
        asset.subject_features = value
        asset.subject_checked = True


def test_legacy_body_conflict_without_corroborated_scene_stays_separate(monkeypatch):
    items = assets(2)
    setup_visuals(monkeypatch, items, [visual('0'), visual('ffffffffffffffff')])
    grouping.assign_groups(items)
    assert [a.group_id for a in items] == [1, 2]


def test_connected_policy_keeps_gradual_transitive_chain(monkeypatch):
    items = assets(3)
    setup_visuals(monkeypatch, items, [visual('0'), visual('ff'), visual('ffff')])
    grouping.assign_groups(items)
    assert [a.group_id for a in items] == [1, 1, 1]


def test_same_pose_not_split_by_count(monkeypatch):
    items = assets(20)
    setup_visuals(monkeypatch, items, [visual('0')] * len(items))
    grouping.assign_groups(items)
    assert {a.group_id for a in items} == {1}


def test_detection_dropout_does_not_alone_split(monkeypatch):
    items = assets(2)
    setup_visuals(monkeypatch, items, [visual('0'), visual(None, None)])
    grouping.assign_groups(items)
    assert [a.group_id for a in items] == [1, 1]


def test_presets_never_impose_time_boundaries(monkeypatch):
    for preset in grouping.PRESETS:
        items = assets(2, 60 * 60 * 24 * 365)
        setup_visuals(monkeypatch, items, [visual('0'), visual('0')])
        grouping.assign_groups(items, preset)
        assert items[1].group_id == 1
