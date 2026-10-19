"""S5M2 output-frame reference display. No tests assert optical focus success."""
from pathlib import Path
from copy import deepcopy
import pytest
from test_af_raw_preview import prepared
from ai_cull_assistant import af_metadata as af, af_raw_preview as rp
from ai_cull_assistant.af_jpeg_preview import project_jpeg_diagnostic


def s5(prepared, orientation=1, mode='16 0', changes=None):
    path,image,report=prepared(orientation)
    source=Path(path);target=source.with_suffix('.rw2');source.rename(target)
    report['source']={**af._source_identity(target),'unchanged_during_read':True}
    old=report['metadata']['raw_tags']
    raw={'SourceFile':old['SourceFile'],'IFD0:Make':'Panasonic','IFD0:Model':'DC-S5M2',
        'IFD0:Orientation':orientation,'Panasonic:FocusMode':5,'Panasonic:AFAreaMode':mode,
        'Panasonic:SweepPanoramaDirection':0,'Panasonic:SweepPanoramaFieldOfView':0,
        'Panasonic:AFSubjectDetection':1,'Panasonic:AFPointPosition':'0.53125 0.3046875',
        'Panasonic:AFAreaSize':'0.099609375 0.1494140625',
        'Panasonic:PanasonicImageWidth':6000,'Panasonic:PanasonicImageHeight':4000,
        'ExifIFD:DigitalZoomRatio':0,'ExifIFD:ExifImageWidth':1920,'ExifIFD:ExifImageHeight':1280,
        'IFD0:CropLeft':4,'IFD0:CropTop':4,'IFD0:CropRight':6004,'IFD0:CropBottom':4004}
    raw.update(changes or {})
    report['metadata'].update(raw_tags=raw,diagnostics=af._diagnostics(raw))
    report['extraction'].update(profile='panasonic_dc_s5m2_ifd0_jpgfromraw2_v1',
        group='IFD0',tag='JpgFromRaw2',qualified_tag='IFD0:JpgFromRaw2')
    return str(target),image,report


@pytest.mark.parametrize('orientation',range(1,9))
@pytest.mark.parametrize('mode',['16 0','240 0'])
def test_native_reference_projects_eight_directions_without_focus_authority(prepared,orientation,mode):
    path,image,report=s5(prepared,orientation,mode)
    before=Path(path).read_bytes()
    bundle=rp.bind_raw_preview(path,image,report)
    assert bundle['geometry']['status']=='not_validated'
    assert bundle['geometry']['reason']==rp.RAW_REASON
    assert 'AF参考区域' in bundle['status_text']
    projected=project_jpeg_diagnostic(bundle,(10,20,10+image.width,20+image.height))
    assert projected.status=='diagnostic' and projected.box is not None
    assert bundle['metadata']['usable_for_subject_selection'] is False
    assert bundle['metadata']['usable_for_keep_reject'] is False
    assert Path(path).read_bytes()==before
    if mode=='240 0':
        assert bundle['metadata']['diagnostics']['mode']['single_area_candidate'] is False
        assert bundle['metadata']['diagnostics']['mode']['display_reference_area'] is True


@pytest.mark.parametrize('changes',[
    {'IFD0:CropLeft':0}, {'IFD0:CropRight':6000}, {'Panasonic:PanasonicImageWidth':1920},
    {'ExifIFD:DigitalZoomRatio':2}, {'ExifIFD:DigitalZoomRatio':None}, {'IFD0:Software':'Adobe Lightroom'},
    {'Panasonic:SweepPanoramaDirection':1}, {'Panasonic:SweepPanoramaFieldOfView':180},
    {'Panasonic:AFAreaMode':'240 1'}, {'Panasonic:FocusMode':2},
])
def test_unproven_raw_frame_processing_and_mode_do_not_draw(prepared,changes):
    path,image,report=s5(prepared,changes=changes)
    bundle=rp.bind_raw_preview(path,image,report)
    assert bundle['geometry']['status']=='unknown'
    assert project_jpeg_diagnostic(bundle,(10,20,10+image.width,20+image.height)).box is None
