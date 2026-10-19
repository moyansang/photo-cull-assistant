"""Synthetic Sony metadata over real JPEG bytes; not a real-camera calibration."""
from copy import deepcopy
from PIL import Image
import pytest
from ai_cull_assistant import af_metadata as af, af_diagnostic as diagnostic, af_jpeg_preview as preview


def sony_raw(orientation=8):
    return {'IFD0:Make':'SONY','IFD0:Model':'ILCE-6600','IFD0:Orientation':orientation,
            'Sony:FocusMode':3,'Sony:Copy1:FocusMode':3,
            'Sony:FocusLocation':'6000 4000 3928 1700','Sony:FullImageSize':'6000 4000',
            'Sony:SonyImageWidth':6000,'Sony:SonyImageHeight':4000,
            'ExifIFD:ExifImageWidth':6000,'ExifIFD:ExifImageHeight':4000,
            'ExifIFD:DigitalZoomRatio':1,'Sony:AFAreaMode':14,'Sony:AFAreaModeSetting':0}


@pytest.fixture
def prepare(tmp_path,monkeypatch):
    parser=tmp_path/'exiftool';parser.write_bytes(b'not executed; parser metadata mocked')
    images=[]
    def make(changes=None,orientation=8):
        raw=sony_raw(orientation);raw.update(changes or {})
        path=tmp_path/'索尼 原片.jpg'
        exif=Image.Exif();exif[274]=orientation
        with Image.new('RGB',(6000,4000),(20,40,60)) as image:
            image.save(path,exif=exif)
        metadata={'schema':af.SCHEMA_VERSION,'status':'not_validated','reason':'camera_playback_location_not_focus_proof',
                  'source':{**af._source_identity(path),'unchanged_during_read':True},
                  'parser':{'name':'ExifTool','path':str(parser),'version':'13.59','adapter_version':af.ADAPTER_VERSION,
                            'stat_identity':af._stat_identity(parser.stat())},
                  'raw_tags':raw,'diagnostics':af._diagnostics(raw),
                  'usable_for_subject_selection':False,'usable_for_keep_reject':False}
        monkeypatch.setattr(af,'read_af_metadata',lambda *a,**kw:deepcopy(metadata))
        image,report=diagnostic.prepare_af_diagnostic(path,max_edge=768)
        images.append(image)
        bundle=preview._bind_prepared(str(path),image,report)
        return bundle,report
    yield make
    for image in images:
        if image is not None:image.close()


@pytest.mark.parametrize('orientation,expected',[(1,(3928/6000,1700/4000)),(8,(1700/4000,1-3928/6000))])
def test_sony_same_jpeg_point_is_reference_only(prepare,orientation,expected):
    bundle,report=prepare(orientation=orientation)
    assert bundle['status']=='diagnostic'
    image=bundle['clean_image'];w,h=image.size
    geometry=preview.project_jpeg_diagnostic(bundle,(10,20,10+w,20+h))
    assert geometry.status=='diagnostic'
    assert geometry.center==pytest.approx((10+expected[0]*w,20+expected[1]*h))
    assert geometry.box is None
    assert report['metadata']['usable_for_subject_selection'] is False
    assert report['usable_for_keep_reject'] is False
    assert report['preview']['exif_transpose_count']==1
    assert report['metadata']['status']=='not_validated'


@pytest.mark.parametrize('changes',[
    {'Sony:SonyImageWidth':5999}, {'ExifIFD:ExifImageHeight':3999},
    {'Sony:FullImageSize':'6000 3999'}, {'Sony:FocusLocation':'6048 4024 3928 1700'},
    {'IFD0:Model':'ILCE-7M4'}, {'Sony:FocusMode':0,'Sony:Copy1:FocusMode':0},
    {'Sony:FocusLocation':'6000 4000 3000 2000'}, {'ExifIFD:DigitalZoomRatio':2},
])
def test_sony_unsafe_or_uncertain_frame_has_no_marks(prepare,changes):
    bundle,_=prepare(changes)
    assert bundle['status']!='diagnostic'
    assert preview.project_jpeg_diagnostic(bundle,(0,0,512,768)).status=='hidden'


def test_sony_center_fallback_has_explanatory_status(prepare):
    bundle,_=prepare({'Sony:FocusLocation':'6000 4000 3000 2000'})
    assert '缺省值' in bundle['status_text']
    assert bundle['metadata']['usable_for_keep_reject'] is False


def test_all_sony_metadata_tags_are_fixed_read_arguments():
    from ai_cull_assistant.af_sony import READ_TAGS
    assert set(READ_TAGS) <= set(af.READ_TAGS)


def test_dimension_report_rejects_float_rounded_fractional_full_size(prepare):
    bundle,report=prepare({'Sony:FullImageSize':'6000.0000000000000001 4000'})
    assert bundle['status']!='diagnostic'
    dimensions=report['preview']['dimension_evidence']
    assert dimensions['status']=='unknown'
    assert dimensions['fields']['FullImageSize']['agrees_with_stored_size'] is False
