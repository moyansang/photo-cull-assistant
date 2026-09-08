from datetime import datetime, timedelta
from pathlib import Path

from ai_cull_assistant import grouping
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.subject import SubjectFeatures


def assets(count, step=.2):
    return [PhotoAsset(str(i), Path(str(i)), Path(str(i)), None, None,
                       datetime(2026, 1, 1) + timedelta(seconds=i * step), '.jpg',
                       preview_path=Path(str(i))) for i in range(count)]


def visual(body, face=(.4, .1, .1, .1)):
    return SubjectFeatures('0', '0', body, face)


def test_fast_pose_change_splits_despite_identical_background(monkeypatch):
    items = assets(2)
    values = [visual('0'), visual('ffffffffffffffff')]
    monkeypatch.setattr(grouping, 'features', lambda p: values[int(str(p))])
    grouping.assign_groups(items)
    assert [a.group_id for a in items] == [1, 2]


def test_anchor_prevents_gradual_drift(monkeypatch):
    items = assets(3)
    values = [visual('0'), visual('ff'), visual('ffff')]
    monkeypatch.setattr(grouping, 'features', lambda p: values[int(str(p))])
    grouping.assign_groups(items)
    assert [a.group_id for a in items] == [1, 1, 2]


def test_same_pose_not_split_by_count(monkeypatch):
    items = assets(20)
    monkeypatch.setattr(grouping, 'features', lambda p: visual('0'))
    grouping.assign_groups(items)
    assert {a.group_id for a in items} == {1}


def test_detection_dropout_does_not_alone_split(monkeypatch):
    items = assets(2)
    values = [visual('0'), visual(None, None)]
    monkeypatch.setattr(grouping, 'features', lambda p: values[int(str(p))])
    grouping.assign_groups(items)
    assert [a.group_id for a in items] == [1, 1]


def test_preset_time_boundaries(monkeypatch):
    monkeypatch.setattr(grouping, 'features', lambda p: visual('0'))
    for preset, limit in [('strict', 1.5), ('standard', 2.5), ('loose', 4.0)]:
        items = assets(2, limit)
        grouping.assign_groups(items, preset)
        assert items[1].group_id == 1
        items = assets(2, limit + .01)
        grouping.assign_groups(items, preset)
        assert items[1].group_id == 2
