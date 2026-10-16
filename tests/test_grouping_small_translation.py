# Historical build15 engine regression; new portrait policy is tested in test_scene_burst_grouping.py.
"""Independent evidence checks for the bounded translation/body-ROI repair."""
from dataclasses import replace
import cv2
import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import grouping,portrait_grouping as p
from ai_cull_assistant.roi_contract import resolve_rois
from ai_cull_assistant.roi_features import features_from_resolved
from hashlib import sha256
from test_grouping_portrait_context import portrait


def set_boxes(asset,face,body):
    resolved=resolve_rois([{'box':list(face)}],[{'box':list(body)}])
    with Image.open(asset.preview_path) as image:
        asset.subject_features=features_from_resolved(image,resolved,
            image_digest=sha256(asset.preview_path.read_bytes()).hexdigest())


def translated_pair(tmp_path,change=None,shift=5):
    # Five source pixels map to two descriptor pixels, avoiding artificial
    # white-noise aliasing in this deliberately high-frequency fixture.
    a,b=portrait(tmp_path,'A'),portrait(tmp_path,'B')
    with Image.open(a.preview_path) as source:
        pixels=np.asarray(source).copy()
    if change=='body':
        pixels[180:440,90:228]=np.random.default_rng(999).integers(20,245,(260,138,3),dtype=np.uint8)
    elif change=='face':
        pixels[96:154,138:177]=np.random.default_rng(999).integers(10,250,(58,39,3),dtype=np.uint8)
    elif change=='background':
        altered=np.random.default_rng(999).integers(20,245,pixels.shape,dtype=np.uint8)
        altered[90:455,80:240]=pixels[90:455,80:240]
        pixels=altered
    elif change=='flat_background':
        altered=np.full_like(pixels,90)
        altered[96:154,138:177]=pixels[96:154,138:177]
        altered[160:450,82:234]=pixels[160:450,82:234]
        pixels=altered
        Image.fromarray(pixels).save(a.preview_path)
        set_boxes(a,(.43,.2,.12,.12),(.25,.32,.5,.62))
    moved=cv2.warpAffine(pixels,np.float32([[1,0,shift],[0,1,0]]),(320,480),borderMode=cv2.BORDER_REFLECT)
    Image.fromarray(moved).save(b.preview_path)
    set_boxes(b,(.43+shift/320,.2,.12,.12),(.25+shift/320,.32,.5,.50))
    return a,b


def test_real_pixels_rescue_roi_crop_drift_without_orb(monkeypatch,tmp_path):
    a,b=translated_pair(tmp_path)
    x,y=map(p.build_portrait_context,(a,b))
    assert x is not None and y is not None
    assert (x.body_hash^y.body_hash).bit_count()>10
    assert p._small_translation_body_match(x,y,'standard')
    assert p._small_translation_body_match(y,x,'standard')
    monkeypatch.setattr(p,'_scene_compatible',lambda *args:False)
    grouping._assign_groups_build15_compat([a,b],preserve_existing=False)
    assert a.group_id==b.group_id


@pytest.mark.parametrize('change',['face','body','background','flat_background'])
def test_valid_context_changes_cannot_use_translation_repair(tmp_path,change):
    a,b=translated_pair(tmp_path,change)
    x,y=map(p.build_portrait_context,(a,b))
    assert x is not None and y is not None
    assert not p._small_translation_body_match(x,y,'standard')
    assert not p._small_translation_body_match(y,x,'standard')


def test_large_phase_translation_is_not_rescued(tmp_path):
    a,b=translated_pair(tmp_path,shift=20)
    x,y=map(p.build_portrait_context,(a,b))
    assert x is not None and y is not None
    assert not p._small_translation_body_match(x,y,'standard')


def test_body_union_disagreement_is_not_erased_by_intersection(tmp_path):
    a,b=translated_pair(tmp_path)
    x,y=map(p.build_portrait_context,(a,b))
    # A real disagreement at the edge belongs to only one ROI. It must still
    # contribute to the body-union evidence instead of cropping to agreement.
    altered=y.photo_rgb.copy();altered[104:122,34:94]=255-altered[104:122,34:94]
    changed=replace(y,photo_rgb=altered)
    matrix=np.float32([[1,0,2],[0,1,0]])
    warp=cv2.warpAffine(x.photo_rgb,matrix,(128,128))
    lm=cv2.warpAffine(p._body_mask(x.body_box).astype(np.uint8),matrix,(128,128))>0
    rm=p._body_mask(y.body_box)
    assert p._region_structure(warp,altered,lm&rm)>.86
    assert p._region_structure(warp,altered,lm|rm)<.86
    assert not p._small_translation_body_match(x,changed,'standard')


def test_stable_companion_face_does_not_replace_changed_principal_body(tmp_path):
    a,b=portrait(tmp_path,'A'),portrait(tmp_path,'B')
    with Image.open(a.preview_path) as image:pixels=np.asarray(image).copy()
    rng=np.random.default_rng(118)
    pixels[160:440,8:78]=rng.integers(20,240,(280,70,3),dtype=np.uint8)
    Image.fromarray(pixels).save(a.preview_path)
    set_boxes(a,(.43,.2,.12,.12),(.02,.32,.25,.62))
    # The selected face remains fixed. A separate principal body changes.
    pixels[180:290,8:78]=255-pixels[180:290,8:78]
    moved=cv2.warpAffine(pixels,np.float32([[1,0,5],[0,1,0]]),(320,480),borderMode=cv2.BORDER_REFLECT)
    Image.fromarray(moved).save(b.preview_path)
    set_boxes(b,(.43+5/320,.2,.12,.12),(.02+5/320,.32,.25,.5))
    x,y=map(p.build_portrait_context,(a,b))
    assert x is not None and y is not None
    assert p._block_ssim(x.face_gray,y.face_gray)>.99
    matrix=np.float32([[1,0,2],[0,1,0]])
    warp=cv2.warpAffine(x.photo_rgb,matrix,(128,128))
    valid=cv2.warpAffine(np.full((128,128),255,np.uint8),matrix,(128,128))==255
    valid=cv2.erode(valid.astype(np.uint8),np.ones((3,3),np.uint8))>0
    assert p._region_structure(warp,y.photo_rgb,valid)>.86
    body=p._body_mask(x.body_box)|p._body_mask(y.body_box)
    assert p._region_structure(warp,y.photo_rgb,body)<.86
    assert not p._small_translation_body_match(x,y,'standard')


def test_background_cell_centres_do_not_override_pixel_masks(tmp_path):
    a,b=translated_pair(tmp_path)
    x,y=map(p.build_portrait_context,(a,b))
    empty=np.zeros((128,128),np.uint8)
    # Only isolated cell centres are visible. Their surrounding texture is
    # entirely excluded; it cannot supply positive statistics.
    empty[8::16,8::16]=255
    x=replace(x,background_mask=empty)
    y=replace(y,background_mask=empty.copy())
    assert not p._translation_background_match(x,y,y.photo_rgb,np.float32([[1,0,0],[0,1,0]]),'standard')


@pytest.mark.parametrize('change',['tiny','flat'])
def test_face_without_matching_detail_does_not_build(tmp_path,change):
    a=portrait(tmp_path,'A')
    if change=='tiny':
        set_boxes(a,(.43,.2,.01,.01),(.25,.32,.5,.62))
    else:
        with Image.open(a.preview_path) as image:pixels=np.asarray(image).copy()
        pixels[90:160,130:185]=90
        Image.fromarray(pixels).save(a.preview_path)
        set_boxes(a,(.43,.2,.12,.12),(.25,.32,.5,.62))
    assert p.build_portrait_context(a) is None


@pytest.mark.parametrize('distance',[10,11])
def test_whole_hash_cutoff_uses_same_lanczos_family_as_original(distance):
    # Real, independently hashed images at either side of the standard cutoff;
    # default Pillow resize differs and cannot silently replace this family.
    for seed in range(100):
        pixels=np.random.default_rng(seed).integers(0,256,(240,320,3),dtype=np.uint8)
        a=Image.fromarray(pixels)
        for shift in range(1,33):
            b=Image.fromarray(np.roll(pixels,shift,axis=1))
            legacy=[int(grouping._image_dhash(im),16) for im in (a,b)]
            if (legacy[0]^legacy[1]).bit_count()!=distance:continue
            if all(p._dhash(im)==value for im,value in zip((a,b),legacy)):continue
            assert [p._whole_dhash(im) for im in (a,b)]==legacy
            assert ((p._whole_dhash(a)^p._whole_dhash(b)).bit_count()<=10)==(distance==10)
            return
    pytest.fail('Deterministic LANCZOS boundary fixture not found')


def test_repeated_background_and_same_costume_cannot_replace_face(tmp_path):
    a,b=translated_pair(tmp_path,'face')
    for asset in (a,b):
        with Image.open(asset.preview_path) as image:pixels=np.asarray(image).copy()
        yy,xx=np.indices(pixels.shape[:2])
        backdrop=np.where(((xx//12+yy//12)%2)[...,None],40,210).astype(np.uint8)
        pixels[:80]=backdrop[:80]
        pixels[:,:70]=backdrop[:,:70]
        pixels[:,250:]=backdrop[:,250:]
        Image.fromarray(pixels).save(asset.preview_path)
        sf=asset.subject_features
        set_boxes(asset,sf.face,sf.body_box)
    x,y=map(p.build_portrait_context,(a,b))
    assert x is not None and y is not None
    assert not p.pose_compatible(x,y)
    assert not p.pose_compatible(y,x)


def test_flat_background_cannot_gain_texture_from_excluded_body_boundary(tmp_path):
    a,b=translated_pair(tmp_path)
    x,y=map(p.build_portrait_context,(a,b))
    pixels=np.full((128,128,3),90,np.uint8)
    pixels[30:110,30:100]=np.random.default_rng(2).integers(0,256,(80,70,3),dtype=np.uint8)
    mask=np.full((128,128),255,np.uint8);mask[30:110,30:100]=0
    x=replace(x,photo_rgb=pixels,background_mask=mask)
    y=replace(y,photo_rgb=pixels.copy(),background_mask=mask.copy())
    assert not p._translation_background_match(x,y,pixels,np.float32([[1,0,0],[0,1,0]]),'standard')
