# Historical build15 engine regression; new portrait policy is tested in test_scene_burst_grouping.py.
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import random
import pytest
from PIL import Image
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant import grouping as g


def asset(i):
    path=Path('/synthetic')/(str(i)+'.png')
    return PhotoAsset(str(i),path,path,None,None,datetime(2020,1,1),'.png')


def partitions(xs):
    groups={}
    for a in xs:groups.setdefault(a.group_id,set()).add(int(a.stem))
    return {frozenset(v) for v in groups.values()}


def test_component_shortcut_preserves_full_graph_partition(monkeypatch):
    base=g._visual_from_image(Image.new('RGB',(64,64),'gray'))
    monkeypatch.setattr(g,'visual_features',lambda a:base)
    monkeypatch.setattr(g,'_crop_aligned_similar',lambda *args:False)
    for seed in range(30):
        rng=random.Random(seed);n=12
        edges={frozenset((i,j)) for i in range(n) for j in range(i+1,n) if rng.random()<.2}
        monkeypatch.setattr(g,'_similar',lambda a,b,*args:frozenset((int(a.stem),int(b.stem))) in edges)
        expected=[];unseen=set(range(n))
        while unseen:
            component={unseen.pop()};pending=list(component)
            while pending:
                left=pending.pop()
                for right in list(unseen):
                    if frozenset((left,right)) in edges:unseen.remove(right);component.add(right);pending.append(right)
            expected.append(frozenset(component))
        xs=[asset(i) for i in range(n)];rng.shuffle(xs);g._assign_groups_build15_compat(xs)
        assert partitions(xs)==set(expected)


def test_protected_bridge_excluded_and_absent_ids_reserved(monkeypatch):
    base=g._visual_from_image(Image.new('RGB',(64,64),'gray'))
    hashes=[0,255,65535]
    monkeypatch.setattr(g,'visual_features',lambda a:replace(base,whole=hashes[int(a.stem)]))
    xs=[asset(i) for i in range(3)];xs[1].group_id=7;xs[1].grouping_anchor_id='manual'
    g._assign_groups_build15_compat(xs,'strict',preserve_existing=False,protected_group_ids=[7,99])
    assert [a.group_id for a in xs]==[100,7,101]
    assert xs[1].grouping_anchor_id=='manual'


def test_partial_roi_schema_migration_rejected_without_modification(monkeypatch):
    xs=[asset(0),asset(1)];xs[0].subject_features=SimpleNamespace(feature_schema='roi-v1');xs[1].subject_features=SimpleNamespace(feature_schema=None)
    with pytest.raises(ValueError,match='schemas differ'):g._assign_groups_build15_compat(xs)
    assert [a.group_id for a in xs]==[0,0]
