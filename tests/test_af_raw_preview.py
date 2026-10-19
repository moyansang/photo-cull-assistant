"""Synthetic source-binding contracts; these are not camera calibration tests."""
from copy import deepcopy
import hashlib
from pathlib import Path

from PIL import Image
import pytest

from ai_cull_assistant import af_metadata as af, af_raw_preview as raw_preview
from ai_cull_assistant.af_jpeg_preview import project_jpeg_diagnostic, _close_bundle


@pytest.fixture
def prepared(tmp_path):
    images = []
    def make(orientation=1, changes=None):
        source=tmp_path/'原片.cr3';source.write_bytes(b'synthetic RAW contract bytes')
        snapshot=tmp_path/'snapshot.cr3';snapshot.write_bytes(source.read_bytes())
        snap=af._source_identity(snapshot);snapshot.unlink()
        parser=tmp_path/'exiftool';parser.write_bytes(b'mocked official reader; not executed')
        raw={'SourceFile':str(snapshot),'IFD0:Make':'Canon','IFD0:Model':'Canon EOS R3','IFD0:Orientation':orientation,
             'Canon:FocusMode':0,'Canon:AFAreaMode':13,'Canon:NumAFPoints':1,'Canon:ValidAFPoints':1,
             'Canon:CanonImageWidth':6000,'Canon:CanonImageHeight':4000,
             'Canon:AFImageWidth':6000,'Canon:AFImageHeight':4000,
             'ExifIFD:ExifImageWidth':6000,'ExifIFD:ExifImageHeight':4000,
             'Canon:AFAreaWidths':[432],'Canon:AFAreaHeights':[432],
             'Canon:AFAreaXPositions':[1908],'Canon:AFAreaYPositions':[-1076],
             'Canon:AFPointsSelected':[1],'Canon:AFPointsInFocus':[1],
             'Canon:DistortionCorrectionSetting':0,
             'Canon:CropLeftMargin':0,'Canon:CropRightMargin':0,'Canon:CropTopMargin':0,'Canon:CropBottomMargin':0,
             'Canon:CroppedImageWidth':6000,'Canon:CroppedImageHeight':4000,
             'Canon:CroppedImageLeft':0,'Canon:CroppedImageTop':0,
             'Canon:DigitalZoom':0,'Canon:ZoomSourceWidth':0,'Canon:ZoomTargetWidth':0}
        raw.update(changes or {})
        metadata={'status':'not_validated','raw_tags':raw,'diagnostics':af._diagnostics(raw),
                  'source':{**snap,'unchanged_during_read':True},
                  'parser':{'name':'ExifTool','path':str(parser),'version':'13.59','adapter_version':af.ADAPTER_VERSION,
                            'stat_identity':af._stat_identity(parser.stat())},
                  'usable_for_subject_selection':False,'usable_for_keep_reject':False}
        frame=(4000,6000) if orientation>=5 else (6000,4000)
        image=Image.new('RGB',(128,192) if orientation>=5 else (192,128),(20,40,60));images.append(image)
        report={'schema':'af-raw-source-v1','status':'viewable','viewable_preview':True,'af_geometry_supported':False,
                'usable_for_subject_selection':False,'usable_for_keep_reject':False,
                'source':{**af._source_identity(source),'unchanged_during_read':True},
                'snapshot':{**snap,'removed':True,'kind':'private_read_only_copy'},'metadata':metadata,
                'binding':dict.fromkeys(('source_unchanged','snapshot_matches_source','snapshot_unchanged','metadata_matches_snapshot','parser_unchanged'),True),
                'extraction':{'profile':'canon_eos_r3_track1_jpgfromraw_v1','group':'Track1','tag':'JpgFromRaw',
                              'qualified_tag':'Track1:JpgFromRaw','encoded_jpeg_sha256':'b'*64,
                              'encoded_jpeg_bytes':1234,'parser':{**metadata['parser'],'unchanged_during_read':True,
                              'sha256':hashlib.sha256(parser.read_bytes()).hexdigest()}},
                'orientation':{'raw_orientation':orientation,'embedded_orientation':None},
                'preview':{'decoded_source_size':[6000,4000],'display_frame_size':list(frame),
                           'raster_size':list(image.size),'clean_pixels_sha256':hashlib.sha256(image.tobytes()).hexdigest(),
                           'decoded_bytes_sha256':'b'*64,'effective_orientation':orientation,'embedded_orientation':None,
                           'exif_transposed':True,'exif_transpose_count':1,'crop':'full_frame',
                           'annotation_applied':False,'pixel_mode':'RGB'}}
        return str(source),image,report
    yield make
    for image in images:image.close()


@pytest.mark.parametrize('orientation',range(1,9))
def test_native_raw_geometry_and_orientation_once(prepared,orientation):
    path,image,report=prepared(orientation)
    before=Path(path).read_bytes()
    bundle=raw_preview.bind_raw_preview(path,image,report)
    w,h=image.size
    projected=project_jpeg_diagnostic(bundle,(10,20,10+w,20+h))
    assert projected.status=='diagnostic'
    assert projected.reason==raw_preview.RAW_REASON
    expected=af.orient_normalized_point_hypothesis([.818,.769],orientation)
    assert projected.center==pytest.approx((10+w*expected[0],20+h*expected[1]))
    assert 'RAW内嵌' in bundle['status_text']
    assert Path(path).read_bytes()==before
    assert bundle['metadata']['source']['path']!=path
    assert bundle['metadata']['usable_for_keep_reject'] is False


@pytest.mark.parametrize('change,reason',[
    ({'Canon:DistortionCorrectionSetting':1},'raw_distortion_mapping_unconfirmed'),
    ({'Canon:DistortionCorrectionSetting':None},'raw_distortion_mapping_unconfirmed'),
    ({'Canon:CropLeftMargin':None},'raw_native_crop_or_zoom_not_established'),
    ({'Canon:DigitalZoom':1},'raw_native_crop_or_zoom_not_established'),
    ({'XMP-xmpMM:HistoryChanged':'/metadata'},'processing_geometry_unconfirmed'),
    ({'Canon:AFPointsSelected':[0],'Canon:AFPointsInFocus':[0]},'mode_not_supported'),
])
def test_viewing_remains_available_when_af_geometry_unknown(prepared,change,reason):
    path,image,report=prepared(changes=change)
    bundle=raw_preview.bind_raw_preview(path,image,report)
    assert bundle['clean_image'] is image and bundle['status']=='viewable'
    assert bundle['reason']==reason
    assert project_jpeg_diagnostic(bundle,(0,0,192,128)).status=='hidden'
    assert bundle['metadata']['usable_for_keep_reject'] is False


@pytest.mark.parametrize('field,value',[
    ('profile','sony_ilce_6600_ifd0_previewimage_v1'),('qualified_tag','QuickTime:PreviewImage')])
def test_no_borrowed_or_alternative_preview_geometry(prepared,field,value):
    path,image,report=prepared();report['extraction'][field]=value
    with pytest.raises(ValueError,match='raw_extraction_profile_unbound'):
        raw_preview.bind_raw_preview(path,image,report)


@pytest.mark.parametrize('mutate,reason',[
    (lambda r:r['snapshot'].update(sha256='f'*64),'raw_snapshot_metadata_unbound'),
    (lambda r:r['metadata']['source'].update(path='/some/other.cr3'),'raw_snapshot_metadata_unbound'),
    (lambda r:r['binding'].update(metadata_matches_snapshot=False),'raw_source_changed_or_unbound'),
    (lambda r:r['binding'].update(snapshot_unchanged=False),'raw_source_changed_or_unbound'),
    (lambda r:r.update(status='error'),'raw_automatic_decisions_not_disabled'),
    (lambda r:r.update(usable_for_subject_selection=True),'raw_automatic_decisions_not_disabled'),
    (lambda r:r['extraction']['parser'].update(version='other'),'raw_parser_changed_or_unbound'),
    (lambda r:r['preview'].update(embedded_orientation=8),'raw_preview_orientation_or_frame_unbound'),
    (lambda r:r['orientation'].update(raw_orientation=True),'raw_preview_orientation_or_frame_unbound'),
    (lambda r:r['metadata']['raw_tags'].update(SourceFile='/other.cr3'),'raw_snapshot_metadata_unbound'),
    (lambda r:r['extraction'].update(encoded_jpeg_bytes=-1),'raw_preview_pixels_or_frame_unbound'),
    (lambda r:r['extraction']['parser'].update(sha256='incorrect'),'raw_parser_changed_or_unbound'),
    (lambda r:r['preview'].update(exif_transpose_count=2),'raw_preview_orientation_or_frame_unbound'),
    (lambda r:r['preview'].update(crop='unknown'),'raw_preview_orientation_or_frame_unbound'),
    (lambda r:r['preview'].update(decoded_bytes_sha256='f'*64),'raw_preview_pixels_or_frame_unbound'),
    (lambda r:r['metadata'].update(usable_for_keep_reject=True),'raw_automatic_decisions_not_disabled'),
])
def test_raw_source_contract_rejects_tampering(prepared,mutate,reason):
    path,image,report=prepared();mutate(report)
    with pytest.raises(ValueError,match=reason):raw_preview.bind_raw_preview(path,image,report)
    with pytest.raises(ValueError):image.getpixel((0,0))


def test_original_changes_after_extraction_fail_closed(prepared):
    path,image,report=prepared();Path(path).write_bytes(b'changed')
    with pytest.raises(ValueError,match='raw_source_changed'):raw_preview.bind_raw_preview(path,image,report)


def test_sealed_raw_geometry_rejects_replacement_or_pixel_changes(prepared):
    path,image,report=prepared();bundle=raw_preview.bind_raw_preview(path,image,report)
    assert project_jpeg_diagnostic(dict(bundle),(0,0,192,128)).status=='hidden'
    image.putpixel((0,0),(1,2,3))
    assert project_jpeg_diagnostic(bundle,(0,0,192,128)).status=='hidden'
    _close_bundle(bundle)
    assert project_jpeg_diagnostic(bundle,(0,0,192,128)).status=='hidden'


def test_raw_loader_keeps_sealed_source_and_closes_on_shutdown(prepared,monkeypatch):
    import time
    from ai_cull_assistant import af_raw_source
    from ai_cull_assistant.af_jpeg_preview import AfJpegPreviewLoader
    path,image,report=prepared(8)
    calls=[]
    def extract(given,**kwargs):
        calls.append(given)
        return image,report
    monkeypatch.setattr(af_raw_source,'load_raw_embedded_preview',extract)
    loader=AfJpegPreviewLoader()
    try:
        token=loader.request(path)
        result=[]
        end=time.monotonic()+3
        while time.monotonic()<end and not result:
            result=loader.pop_completed()
            time.sleep(.01)
        assert result and result[0][0]==token
        bundle=result[0][1]
        assert calls==[path]
        assert bundle['status']=='diagnostic'
        assert project_jpeg_diagnostic(bundle,(0,0,128,192)).status=='diagnostic'
        _close_bundle(bundle)
    finally:
        loader.close()


@pytest.mark.parametrize("kind", ["relative", "symlink"])
def test_canonical_source_binding_accepts_alias_without_borrowing_pixels(prepared,monkeypatch,kind):
    path,image,report=prepared()
    if kind=="relative":
        monkeypatch.chdir(Path(path).parent)
        request=Path(path).name
    else:
        alias=Path(path).with_name("alias.cr3")
        try:alias.symlink_to(path)
        except OSError:pytest.skip("Symlink creation unavailable")
        request=str(alias)
    bundle=raw_preview.bind_raw_preview(request,image,report)
    assert bundle["request_path"]==request
    assert bundle["provenance"]["source"]["path"]==path
    assert project_jpeg_diagnostic(bundle,(0,0,192,128)).status=="diagnostic"


@pytest.mark.parametrize("tamper", ["missing", "conflict", "orientation"])
def test_raw_diagnostics_must_match_preserved_raw_tags(prepared,tamper):
    path,image,report=prepared()
    raw=report["metadata"]["raw_tags"]
    if tamper=="missing":del raw["Canon:AFAreaXPositions"]
    elif tamper=="conflict":raw["Canon:Copy1:AFAreaXPositions"]=[1800]
    else:raw["IFD0:Orientation"]=8
    with pytest.raises(ValueError,match="raw_diagnostics_inconsistent"):
        raw_preview.bind_raw_preview(path,image,report)


def _nikon_bundle_input(prepared, orientation=1, changes=None):
    path, image, report = prepared(orientation)
    source = Path(path).with_suffix('.nef')
    Path(path).rename(source)
    report['source'] = {**af._source_identity(source), 'unchanged_during_read': True}
    old = report['metadata']['raw_tags']
    raw = {key: value for key, value in old.items()
           if not key.startswith(('Canon:', 'ExifIFD:'))}
    raw.update({'IFD0:Make': 'NIKON CORPORATION', 'IFD0:Model': 'NIKON Z5_2',
                'Nikon:AFInfo2Version': '0402', 'Nikon:FocusMode': 'AF-C  ',
                'Nikon:AFDetectionMethod': 2, 'Nikon:AFAreaMode': 207,
                'Nikon:AFCoordinatesAvailable': 1,
                'Nikon:AFImageWidth': 6048, 'Nikon:AFImageHeight': 4032,
                'Nikon:AFAreaXPosition': 3048, 'Nikon:AFAreaYPosition': 1671,
                'Nikon:AFAreaWidth': 204, 'Nikon:AFAreaHeight': 204,
                'Nikon:FocusResult': 1, 'Nikon:RetouchHistory': 0,
                'Nikon:AutoDistortionControl': 0, 'Nikon:CropArea': '8 4 6048 4032',
                'Nikon:CropHiSpeed': '11 6064 4040 6064 4040 0 0',
                'SubIFD1:ImageWidth': 6064, 'SubIFD1:ImageHeight': 4040})
    raw.update(changes or {})
    report['metadata']['raw_tags'] = raw
    report['metadata']['diagnostics'] = af._diagnostics(raw)
    report['extraction'].update(profile='nikon_z5_2_subifd_jpgfromraw_v1',
                                group='SubIFD', tag='JpgFromRaw', qualified_tag='SubIFD:JpgFromRaw')
    report['preview'].update(decoded_source_size=[6048, 4032],
                             display_frame_size=[4032, 6048] if orientation >= 5 else [6048, 4032])
    return str(source), image, report


@pytest.mark.parametrize('orientation', range(1, 9))
def test_nikon_native_raw_output_frame_not_sensor_offset(prepared, orientation):
    path, image, report = _nikon_bundle_input(prepared, orientation)
    before = Path(path).read_bytes()
    bundle = raw_preview.bind_raw_preview(path, image, report)
    width, height = image.size
    result = project_jpeg_diagnostic(bundle, (17, 23, 17 + width, 23 + height))
    assert result.status == 'diagnostic'
    expected = af.orient_normalized_point_hypothesis([3048 / 6048, 1671 / 4032], orientation)
    assert result.center == pytest.approx((17 + width * expected[0], 23 + height * expected[1]))
    assert Path(path).read_bytes() == before
    assert bundle['metadata']['usable_for_subject_selection'] is False
    assert bundle['metadata']['usable_for_keep_reject'] is False


@pytest.mark.parametrize('changes', [
    {'Nikon:RetouchHistory': 19}, {'Nikon:AutoDistortionControl': 1},
    {'Nikon:CropArea': '8 4 6047 4032'}, {'Nikon:CropArea': '20 4 6048 4032'},
    {'SubIFD1:ImageWidth': 6048}, {'Nikon:CropHiSpeed': '12 6064 4040 6064 4040 0 0'},
    {'Nikon:AFInfo2Version': '0401'}, {'Nikon:AFCoordinatesAvailable': 0},
    {'Nikon:FocusMode': 'MF'}, {'XMP-xmpMM:HistoryChanged': '/metadata'},
])
def test_nikon_unknown_mapping_preserves_view_only(prepared, changes):
    path, image, report = _nikon_bundle_input(prepared, changes=changes)
    bundle = raw_preview.bind_raw_preview(path, image, report)
    assert bundle['clean_image'] is image
    assert bundle['status'] == 'viewable'
    assert project_jpeg_diagnostic(bundle, (0, 0, *image.size)).status == 'hidden'


def test_nikon_required_transform_fields_are_actually_requested():
    from ai_cull_assistant.af_nikon import READ_TAGS
    assert set(READ_TAGS) <= set(af.READ_TAGS)


def test_nikon_focus_result_is_not_a_quality_gate(prepared):
    path, image, report = _nikon_bundle_input(prepared, changes={'Nikon:FocusResult': 0})
    bundle = raw_preview.bind_raw_preview(path, image, report)
    assert bundle['status'] == 'diagnostic'
    assert bundle['metadata']['usable_for_keep_reject'] is False


@pytest.mark.parametrize('orientation', range(1, 9))
def test_canon_raw_all_active_records_project_without_subject_choice(prepared, orientation):
    path, image, report = prepared(orientation, changes={
        'Canon:NumAFPoints': 3, 'Canon:ValidAFPoints': 3,
        'Canon:AFAreaWidths': [400, 300, 200], 'Canon:AFAreaHeights': [200, 300, 400],
        'Canon:AFAreaXPositions': [-1000, 0, 1000], 'Canon:AFAreaYPositions': [500, 0, -500],
        'Canon:AFPointsSelected': [3], 'Canon:AFPointsInFocus': [6]})
    bundle = raw_preview.bind_raw_preview(path, image, report)
    width, height = image.size
    result = project_jpeg_diagnostic(bundle, (10, 20, 10+width, 20+height))
    assert result.status == 'diagnostic' and result.center is None and result.box is None
    assert [m.index for m in result.markers] == [0, 1, 2]
    assert [m.role for m in result.markers] == ['selected', 'selected_and_camera_reported_in_focus', 'camera_reported_in_focus']
    def independent(x, y):
        return ((x,y),(1-x,y),(1-x,1-y),(x,1-y),(y,x),(1-y,x),(1-y,1-x),(y,1-x))[orientation-1]
    for marker, x, y in zip(result.markers, [1/3, .5, 2/3], [.375, .5, .625]):
        px, py = independent(x, y)
        assert marker.center == pytest.approx((10+width*px, 20+height*py))
    assert bundle['metadata']['usable_for_subject_selection'] is False
    assert bundle['metadata']['usable_for_keep_reject'] is False
    assert '长虚线' in bundle['status_text']
    bundle['geometry']['records'][0]['role'] = 'camera_reported_in_focus'
    assert project_jpeg_diagnostic(bundle, (10, 20, 10+width, 20+height)).status == 'hidden'
