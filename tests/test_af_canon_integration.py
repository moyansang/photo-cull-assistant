"""Canon schema + actual JPEG decode integration; metadata here is mocked."""
from copy import deepcopy
from PIL import Image
import pytest
from ai_cull_assistant import af_metadata as af, af_diagnostic as diagnostic, af_jpeg_preview as preview


def canon_raw(orientation=1):
    num=1053
    sequence=lambda first:' '.join(map(str,[first]+[0]*(num-1)))
    mask=' '.join(map(str,[1]+[0]*65))
    return {'IFD0:Make':'Canon','IFD0:Model':'Canon EOS R3','IFD0:Orientation':orientation,
            'Canon:FocusMode':1,'Canon:AFAreaMode':22,
            'Canon:NumAFPoints':num,'Canon:ValidAFPoints':1,
            'Canon:CanonImageWidth':6000,'Canon:CanonImageHeight':4000,
            'Canon:AFImageWidth':6000,'Canon:AFImageHeight':4000,
            'ExifIFD:ExifImageWidth':6000,'ExifIFD:ExifImageHeight':4000,
            'Canon:AFAreaWidths':sequence(150),'Canon:AFAreaHeights':sequence(150),
            'Canon:AFAreaXPositions':sequence(457),'Canon:AFAreaYPositions':sequence(831),
            'Canon:AFPointsInFocus':mask,'Canon:AFPointsSelected':mask,
            'Canon:DigitalZoom':0,'Canon:ZoomSourceWidth':0,'Canon:ZoomTargetWidth':0,
            'Canon:CropLeftMargin':0,'Canon:CropRightMargin':0,'Canon:CropTopMargin':0,'Canon:CropBottomMargin':0,
            'Canon:CroppedImageWidth':6000,'Canon:CroppedImageHeight':4000,
            'Canon:CroppedImageLeft':0,'Canon:CroppedImageTop':0,
            'XMP-crs:CropTop':0,'XMP-crs:CropLeft':0,'XMP-crs:CropBottom':1,'XMP-crs:CropRight':1,'XMP-crs:CropAngle':0}


@pytest.fixture
def prepare(tmp_path,monkeypatch):
    parser=tmp_path/'exiftool';parser.write_bytes(b'mocked parser identity; never executed')
    images=[]
    def make(changes=None,orientation=1):
        raw=canon_raw(orientation);raw.update(changes or {})
        path=tmp_path/'佳能 原片.jpg'
        exif=Image.Exif();exif[274]=orientation
        with Image.new('RGB',(6000,4000),(20,40,60)) as image:image.save(path,exif=exif)
        metadata={'schema':af.SCHEMA_VERSION,'status':'not_validated','reason':'camera_reported_AF_not_sharpness_proof',
                  'source':{**af._source_identity(path),'unchanged_during_read':True},
                  'parser':{'name':'ExifTool','path':str(parser),'version':'13.59','adapter_version':af.ADAPTER_VERSION,
                            'stat_identity':af._stat_identity(parser.stat())},
                  'raw_tags':raw,'diagnostics':af._diagnostics(raw),
                  'usable_for_subject_selection':False,'usable_for_keep_reject':False}
        monkeypatch.setattr(af,'read_af_metadata',lambda *a,**kw:deepcopy(metadata))
        image,report=diagnostic.prepare_af_diagnostic(path,max_edge=768)
        images.append(image)
        return preview._bind_prepared(str(path),image,report),report
    yield make
    for image in images:
        if image is not None:image.close()


@pytest.mark.parametrize('orientation,expected',[(1,((3000+457)/6000,(2000-831)/4000)),(8,((2000-831)/4000,1-(3000+457)/6000))])
def test_canon_recorded_point_same_jpeg_orientation(prepare,orientation,expected):
    bundle,report=prepare(orientation=orientation)
    assert bundle['status']=='diagnostic'
    w,h=bundle['clean_image'].size
    geometry=preview.project_jpeg_diagnostic(bundle,(10,20,10+w,20+h))
    assert geometry.status=='diagnostic'
    assert geometry.center==pytest.approx((10+expected[0]*w,20+expected[1]*h))
    assert geometry.box is not None
    assert '相机报告合焦' in bundle['status_text']
    assert report['metadata']['usable_for_subject_selection'] is False
    assert report['usable_for_keep_reject'] is False
    assert report['preview']['exif_transpose_count']==1


@pytest.mark.parametrize('changes',[
    {'Canon:AFImageWidth':5999}, {'ExifIFD:ExifImageHeight':3999},
    {'Canon:CanonImageWidth':5999}, {'IFD0:Model':'Canon EOS R5'},
    {'Canon:FocusMode':3}, {'Canon:AFAreaXPositions':'457'},
    {'XMP-crs:CropLeft':.1}, {'Canon:CropLeftMargin':4},
    {'Canon:AFPointsInFocus':'1'}, {'Canon:Copy1:AFImageWidth':6001},
])
def test_canon_invalid_or_ambiguous_record_cannot_draw(prepare,changes):
    bundle,_=prepare(changes)
    assert bundle['status']!='diagnostic'
    assert preview.project_jpeg_diagnostic(bundle,(0,0,768,512)).status=='hidden'


def test_all_canon_tags_requested():
    from ai_cull_assistant.af_canon import READ_TAGS
    assert set(READ_TAGS)<=set(af.READ_TAGS)

@pytest.mark.parametrize('empty_field,role,label',[
    ('Canon:AFPointsInFocus','selected','选中区域'),
    ('Canon:AFPointsSelected','camera_reported_in_focus','相机报告合焦'),
])
def test_canon_selected_and_reported_focus_roles_stay_separate(prepare,empty_field,role,label):
    bundle,_=prepare({empty_field:' '.join(['0']*66)})
    assert bundle['status']=='diagnostic'
    assert bundle['metadata']['diagnostics']['coordinates']['display_role']==role
    assert label in bundle['status_text']
    assert bundle['metadata']['usable_for_subject_selection'] is False


def test_canon_different_selected_and_reported_points_not_collapsed(prepare):
    def arr(a,b):return ' '.join(map(str,[a,b]+[0]*1051))
    changes={'Canon:ValidAFPoints':2,'Canon:AFAreaWidths':arr(150,150),
             'Canon:AFAreaHeights':arr(150,150),'Canon:AFAreaXPositions':arr(457,-100),
             'Canon:AFAreaYPositions':arr(831,200),'Canon:AFPointsSelected':' '.join(['2']+['0']*65)}
    bundle,_=prepare(changes)
    assert bundle['status']=='diagnostic'
    assert '2条记录' in bundle['status_text']
    coordinates=bundle['metadata']['diagnostics']['coordinates']
    assert coordinates['active_point_indices']==[0,1]
    assert coordinates['display_point_index'] is None
    projected=preview.project_jpeg_diagnostic(bundle,(0,0,768,512))
    assert projected.status=='diagnostic' and projected.center is None
    assert [m.index for m in projected.markers]==[0,1]
    assert [m.role for m in projected.markers]==['camera_reported_in_focus','selected']
    assert bundle['metadata']['usable_for_subject_selection'] is False
    assert bundle['metadata']['usable_for_keep_reject'] is False
