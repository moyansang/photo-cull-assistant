"""Grouping follows burst/scene/cohort evidence, never technical quality state."""
from copy import deepcopy
from dataclasses import replace
from dataclasses import fields
from datetime import datetime,timedelta
from types import SimpleNamespace
import cv2
import numpy as np
from PIL import Image,ImageFilter
import pytest
from ai_cull_assistant import grouping
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant import scene_burst_grouping as scene


def photo(tmp_path,name,*,second=0,flat=False,principal=(205,40,35),companion=(25,170,50),scene_seed=42):
    rng=np.random.default_rng(scene_seed)
    pixels=np.full((360,480,3),95,np.uint8) if flat else rng.integers(20,215,(360,480,3),dtype=np.uint8)
    pixels[110:340,80:210]=principal;pixels[110:340,275:405]=companion
    pixels[80:125,117:167]=(210,175,145);pixels[80:125,315:365]=(180,155,125)
    path=tmp_path/(name+'.png');Image.fromarray(pixels).save(path)
    faces=[{'box':(.24,.22,.11,.13),'origin':'automatic'}, {'box':(.65,.22,.11,.13),'origin':'automatic'}]
    subject=SimpleNamespace(feature_schema='subject-explicit-roi-dhash64-v1',analysis_faces=faces,analysis_bodies=[],face=None)
    return PhotoAsset(name,path,path,None,path,datetime(2026,1,1)+timedelta(seconds=second),'.png',
        preview_path=path,subject_features=subject,subject_checked=True)


def partition(assets):
    grouping.assign_groups(assets,preserve_existing=False)
    return {frozenset(a.stem for a in assets if a.group_id==g) for g in {a.group_id for a in assets}}


def test_all_quality_states_and_quality_notes_do_not_change_partition(tmp_path):
    a,b=photo(tmp_path,'a'),photo(tmp_path,'b',second=.2)
    expected=partition([a,b]);assert len(expected)==1
    for score,rejected,result,notes in [(0.,True,{'status':'blur'},['face_blur','body_blur']),
            (999.,False,{'status':'clear'},['severe_subject_blur']), (None,True,None,['quality_unavailable'])]:
        a.focus_score=score;a.auto_rejected=rejected;a.ai_focus_result=result;a.notes=notes
        a.clarity_evidence={'recommendation':'reject' if rejected else 'keep'}
        a.local_recommendation='reject' if rejected else 'keep';a.ai_focus_stale=True;a.ai_focus_dirty=True
        assert partition([a,b])==expected


@pytest.mark.parametrize('part',['face','body','both'])
def test_actual_local_blur_does_not_split_same_camera_and_cohort(tmp_path,part):
    a,b=photo(tmp_path,'a'),photo(tmp_path,'b',second=.2)
    with Image.open(b.preview_path) as image:
        boxes=[]
        if part in ('face','both'):boxes.extend([(110,75,175,135),(305,75,375,135)])
        if part in ('body','both'):boxes.extend([(75,130,215,345),(270,130,410,345)])
        for box in boxes:image.paste(image.crop(box).filter(ImageFilter.GaussianBlur(9)),box)
        image.save(b.preview_path)
    assert len(partition([a,b]))==1


def test_shared_companion_does_not_join_changed_principal(tmp_path):
    a=photo(tmp_path,'a');b=photo(tmp_path,'b',second=.2,principal=(30,45,220))
    accepted,reason=scene.pair_evidence(scene.build_context(a),scene.build_context(b))
    assert reason['camera']=='same'
    assert not accepted and len(partition([a,b]))==2


def test_same_colour_shared_companion_needs_injective_principal_support(tmp_path):
    context=scene.build_context(photo(tmp_path,'base'))
    colour=np.zeros(34,np.float32);colour[[1,5,30]]=[.4,.3,.3]
    identity=np.eye(3,dtype=np.float32);patterns=np.eye(10,dtype=np.float32)
    companion=scene.PersonAppearance(colour,identity[0],patterns[2])
    first=scene.PersonAppearance(colour,identity[1],patterns[0])
    changed=scene.PersonAppearance(colour,identity[2],patterns[1])
    left=replace(context,people=(companion,first))
    right=replace(context,time=context.time+.2,people=(companion,changed))
    accepted,why=scene.pair_evidence(left,right)
    assert why['cohort_overlap']==pytest.approx(1.)
    assert not accepted and why['people_coverage']['conflict']
    # Information loss is unknown, not a claim that a person was replaced.
    unknown=scene.PersonAppearance(colour,None,None)
    assert scene.pair_evidence(left,replace(right,people=(companion,unknown)))[0]
    extra=scene.PersonAppearance(colour,np.array([0.,0.,-1.],np.float32),patterns[3])
    expanded=replace(right,people=(companion,changed,extra))
    accepted,why=scene.pair_evidence(left,expanded)
    assert not accepted and why['people_coverage']['conflict']
    assert scene.pair_evidence(left,replace(right,people=(companion,unknown,unknown)))[0]


def test_quality_mutations_preserve_full_multi_burst_and_manual_partition(tmp_path):
    assets=[photo(tmp_path,n,second=t) for n,t in [('a',0),('b',.2),('c',150),('d',150.2),('e',500)]]
    assets=[SimpleNamespace(**{f.name:getattr(a,f.name) for f in fields(PhotoAsset)}) for a in assets]
    assets[-1].group_id=77;assets[-1].grouping_anchor_id='manual'
    def run():
        grouping.assign_groups(assets,preserve_existing=False,protected_group_ids={77})
        return {frozenset(a.stem for a in assets if a.group_id==g) for g in {a.group_id for a in assets}}
    expected=run();assert len(expected)==3
    for i,a in enumerate(assets):
        a.auto_rejected=bool(i%2);a.screening_reason='confirmed_primary_severe_blur'
        a.focus_score=0.;a.ai_focus_result={'status':'blur'};a.ai_focus_dirty=True;a.ai_focus_stale=True
        a.clarity_version='future-quality-policy';a.clarity_evidence={'recommendation':'reject'}
        a.local_recommendation='reject';a.person_review_pending=True
        a.notes=['face_blur','body_blur','quality_unavailable']
        a.decision='remove';a.rating=1;a.excluded=True
    assert run()==expected and assets[-1].group_id==77


def test_incremental_new_photo_uses_scene_policy_and_keeps_existing_ids(tmp_path,monkeypatch):
    a,b=photo(tmp_path,'a'),photo(tmp_path,'b',second=.2)
    a.group_id=9;a.grouping_anchor_id=grouping.source_photo_id(a)
    expected_anchor=a.grouping_anchor_id
    monkeypatch.setattr(grouping,'_assign_groups_fixed_legacy',lambda *a,**k:(_ for _ in ()).throw(AssertionError('old pose veto')))
    grouping.assign_groups([a,b],preserve_existing=True)
    assert a.group_id==b.group_id==9 and a.grouping_anchor_id==expected_anchor


def test_existing_reference_resolves_saved_anchor_not_first_member(tmp_path):
    a=photo(tmp_path,'a',scene_seed=129)
    b=photo(tmp_path,'b',second=.2)
    c=photo(tmp_path,'c',second=.4)
    a.group_id=b.group_id=9
    a.grouping_anchor_id=b.grouping_anchor_id=grouping.source_photo_id(b)
    grouping.assign_groups([a,b,c],preserve_existing=True)
    assert c.group_id==9 and c.grouping_anchor_id==grouping.source_photo_id(b)
    d=photo(tmp_path,'d',second=.6);a.grouping_anchor_id=b.grouping_anchor_id='absent-anchor'
    grouping.assign_groups([a,b,d],preserve_existing=True)
    assert a.group_id==b.group_id==9 and d.group_id!=9


def test_same_cohort_different_scene_and_time_interruptions_stay_separate(tmp_path):
    a=photo(tmp_path,'a');b=photo(tmp_path,'b',second=.2,scene_seed=129)
    c=photo(tmp_path,'c',second=120)
    assert len(partition([a,c]))==2
    # A new scene with the same foreground is not corroborated by color alone.
    assert len(partition([a,b]))==2


def test_low_texture_background_is_unknown_not_different(tmp_path):
    a,b=photo(tmp_path,'a',flat=True),photo(tmp_path,'b',flat=True,second=.2)
    x,y=map(scene.build_context,(a,b))
    assert scene.camera_evidence(x,y)[0]=='unknown'
    assert len(partition([a,b]))==1


def test_group_camera_anchor_prevents_transitive_camera_drift(tmp_path):
    a=photo(tmp_path,'a');base=scene.build_context(a)
    # Camera matrices are independently tested on pixels elsewhere. This graph
    # fixture checks the group reference never slides from A to B to C.
    contexts=[replace(base,time=t,frame_hash=i) for i,t in enumerate((0.,1.,2.))]
    from unittest.mock import patch
    def evidence(left,right,cfg=scene.CONFIG):
        return (abs(left.frame_hash-right.frame_hash)<=1,dict(camera='same',cohort_overlap=1.,
            geometry={'ratio':1.},people_coverage={'strong_identity':False}))
    assets=[photo(tmp_path,n,second=i) for i,n in enumerate('abc')]
    with patch.object(scene,'build_context',side_effect=contexts),patch.object(scene,'pair_evidence',side_effect=evidence):
        assert len(partition(assets))==2
    assert assets[0].group_id==assets[1].group_id!=assets[2].group_id


def test_protected_manual_groups_cancel_atomicity_and_order_independence(tmp_path):
    assets=[photo(tmp_path,n,second=i) for i,n in enumerate('abc')]
    expected=partition(assets)
    assert partition(list(reversed(assets)))==expected
    assets[0].group_id=73;assets[0].grouping_anchor_id='manual'
    grouping.assign_groups(assets,preserve_existing=False,protected_group_ids={73,100})
    assert assets[0].group_id==73 and assets[0].grouping_anchor_id=='manual'
    assert all(a.group_id>100 for a in assets[1:])
    before=[(a.group_id,a.grouping_anchor_id) for a in assets];calls=0
    def cancel():
        nonlocal calls
        calls+=1;return calls>=4
    with pytest.raises(grouping.GroupingCancelled):grouping.assign_groups(assets,preserve_existing=False,cancel_check=cancel)
    assert before==[(a.group_id,a.grouping_anchor_id) for a in assets]


def test_decode_error_is_input_failure_not_a_quality_recommendation(tmp_path):
    a,b=photo(tmp_path,'a'),photo(tmp_path,'b',second=.2)
    b.notes=['decode_error: generated unreadable placeholder']
    assert len(partition([a,b]))==2
    b.notes=[];b.preview_path.write_bytes(b'broken image bytes')
    assert len(partition([a,b]))==2


def test_missing_saved_anchor_is_preserved_across_group_store_roundtrip(tmp_path):
    from ai_cull_assistant.group_store import save_groups,load_groups
    a,b=photo(tmp_path,'a'),photo(tmp_path,'b',second=.2)
    grouping.assign_groups([a,b],preserve_existing=False)
    saved_anchor=b.grouping_anchor_id
    store=tmp_path/'groups.json';save_groups([a,b],store,source='auto',collection_key='test')
    reopened=[photo(tmp_path,'a'),photo(tmp_path,'b',second=.2)]
    assert load_groups(reopened,store,collection_key='test')
    assert reopened[1].grouping_anchor_id==saved_anchor
    survivor=reopened[1];c=photo(tmp_path,'c',second=.4)
    grouping.assign_groups([survivor,c],preserve_existing=True)
    assert survivor.group_id==1 and survivor.grouping_anchor_id==saved_anchor and c.group_id!=1


def test_incremental_photo_never_bridges_two_existing_groups(tmp_path):
    a,b,c=[photo(tmp_path,n,second=i*.2) for i,n in enumerate('abc')]
    a.group_id=9;a.grouping_anchor_id=grouping.source_photo_id(a)
    c.group_id=12;c.grouping_anchor_id=grouping.source_photo_id(c)
    grouping.assign_groups([a,b,c],preserve_existing=True)
    assert a.group_id==9 and c.group_id==12 and b.group_id in (9,12)


def test_camera_rotation_is_measured_at_frame_centre(tmp_path):
    a,b=photo(tmp_path,'a'),photo(tmp_path,'b',second=.2)
    with Image.open(a.preview_path) as image:pixels=np.asarray(image)
    matrix=cv2.getRotationMatrix2D((240,180),4.,1.)
    Image.fromarray(cv2.warpAffine(pixels,matrix,(480,360),borderMode=cv2.BORDER_REFLECT)).save(b.preview_path)
    state,info=scene.camera_evidence(scene.build_context(a),scene.build_context(b))
    assert state=='same' and max(abs(v) for v in info['translation'])<.02


def test_unknown_camera_rank_cannot_use_unreliable_inlier_ratio(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    contexts=[replace(base,time=float(i),frame_hash=i) for i in range(4)]
    assets=[photo(tmp_path,n,second=i) for i,n in enumerate('abcd')]
    monkeypatch.setattr(scene,'build_context',lambda a,identity=None:contexts[ord(a.stem)-ord('a')])
    def evidence(left,right,cfg=scene.CONFIG):
        # The second camera remains separate. Both later photos are accepted
        # by either fixed reference, with opposite weak affine ratios.
        accepted=right.frame_hash!=1
        ratio=.9 if (left.frame_hash+right.frame_hash)%2 else .1
        return accepted,dict(camera='unknown',geometry={'ratio':ratio},cohort_overlap=1.,
            people_coverage={'strong_identity':True,'unknown':False,'conflict':False})
    monkeypatch.setattr(scene,'pair_evidence',evidence)
    grouping.assign_groups(assets,preserve_existing=False)
    assert assets[0].group_id==assets[2].group_id==assets[3].group_id!=assets[1].group_id


def test_missing_person_information_is_not_a_whole_frame_pose_veto(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    colour=np.zeros(34,np.float32);colour[0]=1.
    unknown=scene.PersonAppearance(colour,None,None)
    left=replace(base,people=(unknown,),grey=np.zeros((96,96),np.uint8))
    right=replace(base,people=(unknown,),grey=np.full((96,96),180,np.uint8))
    monkeypatch.setattr(scene,'camera_evidence',lambda *args:('unknown',{}))
    assert scene.pair_evidence(left,right)[0]


def test_known_weak_identity_is_not_rescued_by_global_colour(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    a=np.zeros(34,np.float32);a[0]=1.
    b=np.zeros(34,np.float32);b[:2]=[.61,.39]
    vector=np.array([.22,np.sqrt(1.-.22**2)],np.float32)
    pattern=np.ones(10,np.float32)/np.sqrt(10.)
    left=replace(base,people=(scene.PersonAppearance(a,np.array([1.,0.],np.float32),pattern),))
    right=replace(base,people=(scene.PersonAppearance(b,vector,pattern),))
    monkeypatch.setattr(scene,'camera_evidence',lambda *args:('unknown',{}))
    accepted,why=scene.pair_evidence(left,right)
    assert not accepted and why['cohort_overlap']==pytest.approx(1.)
    assert not why['people_coverage']['unknown'] and not why['people_coverage']['conflict']


def test_large_framing_change_needs_matched_identity_and_coherent_geometry(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    colour=np.zeros(34,np.float32);colour[0]=1.
    identity=np.array([1.,0.],np.float32);pattern=np.ones(10,np.float32)/np.sqrt(10.)
    near=scene.PersonAppearance(colour,identity,pattern,(.3,.2,.2,.25))
    far=replace(near,box=(.3,.2,.08,.10))
    left=replace(base,people=(near,));right=replace(base,people=(far,))
    monkeypatch.setattr(scene,'camera_evidence',lambda *args:('unknown',{}))
    accepted,why=scene.pair_evidence(left,right)
    assert not accepted and why['framing']['state']=='different_framing'
    assert scene.pair_evidence(left,replace(right,people=(replace(far,box=None),)))[0]
    assert scene.pair_evidence(left,replace(right,people=(replace(far,box=(.3,.2,.15,.23)),)))[0]
    # Turned profiles or missing identity cannot invent a framing conflict.
    assert not scene._framing_evidence((near,),(replace(far,box=(.3,.2,.03,.10)),))[0]
    assert not scene._framing_evidence((near,),(replace(far,vector=None),))[0]


def test_unknown_camera_checks_background_layout_but_ignores_flat_exposure(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    monkeypatch.setattr(scene,'camera_evidence',lambda *args:('unknown',{}))
    # Same global palette/person evidence cannot hide a different arrangement
    # of a sufficiently large shared background.
    pattern=np.tile(np.concatenate((np.zeros(48),np.full(48,220))),(96,1)).astype(np.uint8)
    left=replace(base,grey=pattern,mask=np.full((96,96),255,np.uint8))
    right=replace(left,grey=np.fliplr(pattern))
    accepted,why=scene.pair_evidence(left,right)
    assert not accepted and why['background_layout']['sufficient']
    assert why['background_layout']['error']>.20
    # A flat backdrop or exposure shift is unknown, not another camera.
    flat=replace(left,grey=np.full((96,96),30,np.uint8))
    assert scene.pair_evidence(flat,replace(flat,grey=np.full((96,96),190,np.uint8)))[0]
    # An insufficient strip cannot assert a scene conflict.
    strip=np.zeros((96,96),np.uint8);strip[:4]=255
    assert scene.pair_evidence(replace(left,mask=strip),replace(right,mask=strip))[0]


def test_first_corroborated_multi_person_reference_can_use_strong_identity(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    colour=np.zeros(34,np.float32);colour[0]=1.
    ids=np.eye(3,dtype=np.float32);patterns=np.eye(10,dtype=np.float32)
    companion=scene.PersonAppearance(colour,ids[0],patterns[0])
    principal=scene.PersonAppearance(colour,ids[1],patterns[1])
    newcomer=scene.PersonAppearance(colour,ids[2],patterns[2])
    contexts=[replace(base,time=float(i),people=p,descriptors=None) for i,p in
              enumerate(((companion,),(companion,principal),(companion,newcomer)))]
    assets=[photo(tmp_path,n,second=i) for i,n in enumerate('abc')]
    monkeypatch.setattr(scene,'build_context',lambda a,identity=None:contexts[ord(a.stem)-ord('a')])
    grouping.assign_groups(assets,preserve_existing=False)
    assert assets[0].group_id==assets[1].group_id!=assets[2].group_id


def test_dynamic_background_with_fixed_identity_scale_is_not_a_pose_veto(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    monkeypatch.setattr(scene,'camera_evidence',lambda *args:('unknown',{}))
    colour=np.zeros(34,np.float32);colour[0]=1.
    person=scene.PersonAppearance(colour,np.array([1.,0.],np.float32),np.ones(10,np.float32)/np.sqrt(10),(.3,.2,.1,.12))
    pattern=np.tile(np.concatenate((np.zeros(48),np.full(48,220))),(96,1)).astype(np.uint8)
    left=replace(base,people=(person,),grey=pattern,mask=np.full((96,96),255,np.uint8))
    right=replace(left,grey=np.fliplr(pattern))
    assert scene.pair_evidence(left,right)[0]
    changed=replace(person,box=(.3,.2,.07,.084))
    assert not scene.pair_evidence(left,replace(right,people=(changed,)))[0]


def test_partial_cohort_without_camera_needs_strong_full_cohort_support(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    colour=np.zeros(34,np.float32);colour[0]=1.
    identities=np.eye(3,dtype=np.float32);pattern=np.ones(10,np.float32)/np.sqrt(10)
    people=tuple(scene.PersonAppearance(colour,v,pattern) for v in identities)
    left=replace(base,people=people)
    full=np.zeros(34,np.float32);full[0]=1.
    partial=np.zeros(34,np.float32);partial[:2]=[.77,.23]
    left=replace(left,cohort=full)
    right=replace(base,people=people[:2],cohort=partial)
    monkeypatch.setattr(scene,'camera_evidence',lambda *args:('unknown',{}))
    accepted,why=scene.pair_evidence(left,right)
    assert not accepted and why['people_coverage']['strong_identity']
    assert scene.pair_evidence(left,replace(right,cohort=full))[0]
    # A face dropout is not a veto when camera correspondence is positive.
    monkeypatch.setattr(scene,'camera_evidence',lambda *args:('same',{}))
    assert scene.pair_evidence(left,right)[0]


def test_reopened_group_reconstructs_first_corroborated_cohort_without_moving_camera_anchor(tmp_path,monkeypatch):
    base=scene.build_context(photo(tmp_path,'base'))
    colour=np.zeros(34,np.float32);colour[0]=1.
    ids=np.eye(3,dtype=np.float32);patterns=np.eye(10,dtype=np.float32)
    companion=scene.PersonAppearance(colour,ids[0],patterns[0])
    original=scene.PersonAppearance(colour,ids[1],patterns[1])
    changed=scene.PersonAppearance(colour,ids[2],patterns[2])
    contexts=[replace(base,time=float(i),people=p,descriptors=None) for i,p in
              enumerate(((companion,),(companion,original),(companion,changed)))]
    assets=[photo(tmp_path,n,second=i) for i,n in enumerate('abc')]
    monkeypatch.setattr(scene,'build_context',lambda a,identity=None:contexts[ord(a.stem)-ord('a')])
    camera_anchor=grouping.source_photo_id(assets[0])
    for a in assets[:2]:a.group_id=9;a.grouping_anchor_id=camera_anchor
    grouping.assign_groups(assets,preserve_existing=True)
    assert assets[0].group_id==assets[1].group_id==9!=assets[2].group_id
    assert assets[0].grouping_anchor_id==assets[1].grouping_anchor_id==camera_anchor
