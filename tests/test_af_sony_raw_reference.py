"""Bounded uncalibrated playback-point display; never subject authority."""
from pathlib import Path
from copy import deepcopy
import pytest
from test_af_raw_preview import prepared
from ai_cull_assistant import af_metadata as af, af_raw_preview as rp
from ai_cull_assistant.af_jpeg_preview import project_jpeg_diagnostic


def sony(prepared,orientation=1,changes=None):
    path,image,report=prepared(orientation)
    source=Path(path);target=source.with_suffix('.arw');source.rename(target)
    report['source']={**af._source_identity(target),'unchanged_during_read':True}
    raw={'SourceFile':report['metadata']['raw_tags']['SourceFile'],'IFD0:Make':'SONY','IFD0:Model':'ILCE-6600',
         'IFD0:Orientation':orientation,'Sony:FocusMode':3,'Sony:AFAreaMode':0,'Sony:AFAreaModeSetting':0,
         'Sony:FocusLocation':'6000 4000 3890 2008','Sony:FullImageSize':'6000 4000',
         'Sony:SonyImageWidth':6000,'Sony:SonyImageHeight':4000,'ExifIFD:ExifImageWidth':6000,'ExifIFD:ExifImageHeight':4000,
         'SubIFD:ImageWidth':6048,'SubIFD:ImageHeight':4024,'SubIFD:DefaultCropOrigin':'12 12',
         'SubIFD:DefaultCropSize':'6000 4000','SubIFD:SonyCropTopLeft':'12 12','SubIFD:SonyCropSize':'6000 4000',
         'ExifIFD:DigitalZoomRatio':1,'Sony:AutoPortraitFramed':0,'Sony:DistortionCorrectionSetting':2}
    raw.update(changes or {})
    report['metadata'].update(raw_tags=raw,diagnostics=af._diagnostics(raw))
    report['extraction'].update(profile='sony_ilce_6600_ifd0_previewimage_v1',group='IFD0',tag='PreviewImage',qualified_tag='IFD0:PreviewImage')
    report['preview'].update(decoded_source_size=[1616,1080],display_frame_size=[1080,1616] if orientation>=5 else [1616,1080])
    return str(target),image,report


@pytest.mark.parametrize('orientation',[1,8])
@pytest.mark.parametrize('area_mode',[0,14])
def test_reference_point_has_distinct_semantics_no_box_or_authority(prepared,orientation,area_mode):
    path,image,report=sony(prepared,orientation,{'Sony:AFAreaMode':area_mode})
    before=Path(path).read_bytes();bundle=rp.bind_raw_preview(path,image,report)
    projected=project_jpeg_diagnostic(bundle,(10,20,10+image.width,20+image.height))
    assert projected.status=='diagnostic' and projected.box is None
    expected=af.orient_normalized_point_hypothesis([3890/6000,2008/4000],orientation)
    assert projected.center==pytest.approx((10+image.width*expected[0],20+image.height*expected[1]))
    assert projected.reason==rp.SONY_REFERENCE_REASON and projected.reason!=rp.RAW_REASON
    assert '未校准' in bundle['status_text'] and '回放参考点' in bundle['status_text']
    assert '搜索' not in bundle['status_text']
    assert bundle['metadata']['usable_for_subject_selection'] is False
    assert Path(path).read_bytes()==before


@pytest.mark.parametrize('orientation',[2,3,4,5,6,7])
def test_uncovered_or_mirrored_orientations_remain_hidden(prepared,orientation):
    path,image,report=sony(prepared,orientation);bundle=rp.bind_raw_preview(path,image,report)
    assert project_jpeg_diagnostic(bundle,(0,0,image.width,image.height)).status=='hidden'


@pytest.mark.parametrize('changes',[
    {'Sony:FocusMode':2},{'Sony:AFAreaMode':1},{'Sony:AFAreaModeSetting':1},
    {'Sony:FocusLocation':'6000 4000 3000 2000'}, {'Sony:FocusLocation':'6000 4000 7000 2008'},
    {'Sony:FocusFrameSize':'100 100 1'}, {'Sony:FocusFrameSize':'100 100 0'},
    {'SubIFD:ImageWidth':6000},{'SubIFD:DefaultCropOrigin':'0 0'},
    {'ExifIFD:ExifImageWidth':None},{'ExifIFD:ExifImageHeight':None},
    {'Sony:SonyImageWidth':None},{'Sony:SonyImageHeight':None},
    {'SubIFD:SonyCropSize':'5990 4000'}, {'ExifIFD:DigitalZoomRatio':None},
    {'ExifIFD:DigitalZoomRatio':2},{'Sony:AutoPortraitFramed':1},
    {'Sony:DistortionCorrectionSetting':None},{'IFD0:Software':'Adobe Lightroom'},
])
def test_unknown_mode_crop_or_processed_source_does_not_draw(prepared,changes):
    path,image,report=sony(prepared,changes=changes);bundle=rp.bind_raw_preview(path,image,report)
    assert project_jpeg_diagnostic(bundle,(0,0,image.width,image.height)).status=='hidden'
