"""Bind camera-embedded RAW preview pixels without borrowing sibling JPEGs.

Exact Canon R3 and Nikon Z5II native-frame profiles can acquire diagnostic AF
geometry here when their recorded crop and processing constraints hold. Other supported extraction profiles may supply a viewable image
while their AF mapping stays unknown. This binding alone grants no quality or subject authority; the separate AF-person contract can reuse a strictly profiled native frame.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any

from PIL import Image

from . import af_canon, af_nikon, af_sony, af_diagnostic, af_metadata
from .af_jpeg_preview import (_PreparedBundle, _PixelBinding, _fingerprint,
                              _numbers, _parser_key, _size, _source_key, _sealed_markers)

RAW_SUFFIXES = {'.cr3', '.nef', '.arw', '.rw2'}
RAW_FRAME = 'exif_transposed_native_RAW_embedded_jpeg_normalized_edges'
RAW_REASON = 'same_raw_embedded_preview_unvalidated_AF_record'
SONY_REFERENCE_REASON = 'sony_raw_playback_point_uncalibrated_display_only'


def _geometry(metadata: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    result = {'status': 'unknown', 'reason': 'raw_AF_mapping_not_supported',
              'center_xy_hypothesis': None, 'bbox_xywh_hypothesis': None,
              'center_drawn': False, 'box_drawn': False, 'coordinate_frame': RAW_FRAME,
              'meaning': 'RAW camera AF record; not focus success, eye or optical sharpness'}
    extraction, preview = report['extraction'], report['preview']
    if (extraction.get('profile') == 'sony_ilce_6600_ifd0_previewimage_v1'
            and extraction.get('qualified_tag') == 'IFD0:PreviewImage'
            and af_diagnostic._display_profile(metadata) == af_sony.PROFILE):
        # Independent A7Info documentation explicitly describes A6600 RAW
        # embedded-preview playback-point display. This is that normalized
        # display convention, not a calibrated sensor/preview transform.
        raw = metadata.get('raw_tags', {})
        diag = metadata.get('diagnostics', {})
        result['reason'] = 'sony_reference_profile_not_established'
        if (_size(preview.get('decoded_source_size')) != (1616,1080)
                or preview.get('effective_orientation') not in (1,8)
                or diag.get('coordinates', {}).get('reference_frame_size') != [6000,4000]):
            return result
        exact = [('ImageWidth', {'SubIFD'}, 6048), ('ImageHeight', {'SubIFD'}, 4024),
                 ('ExifImageWidth', {'ExifIFD'}, 6000), ('ExifImageHeight', {'ExifIFD'}, 4000),
                 ('SonyImageWidth', {'Sony'}, 6000), ('SonyImageHeight', {'Sony'}, 4000),
                 ('DigitalZoomRatio', {'ExifIFD'}, 1), ('AutoPortraitFramed', {'Sony'}, 0),
                 ('DistortionCorrectionSetting', {'Sony'}, 2)]
        for name, groups, expected in exact:
            value, issue = af_metadata._unique_tag(raw, name, groups)
            if issue is not None or isinstance(value,bool) or value != expected:
                return result
        for name, expected in [('DefaultCropOrigin',[12,12]),('DefaultCropSize',[6000,4000]),
                               ('SonyCropTopLeft',[12,12]),('SonyCropSize',[6000,4000])]:
            value, issue = af_metadata._unique_tag(raw, name, {'SubIFD'})
            if issue is not None or af_metadata._numbers(value,2) != expected:
                return result
        mode = diag.get('mode', {})
        if (mode.get('FocusMode', {}).get('raw_value') != 3
                or mode.get('AFAreaMode', {}).get('raw_value') not in (0,14)
                or mode.get('AFAreaModeSetting', {}).get('raw_value') != 0
                or diag.get('coordinates', {}).get('area_size', {}).get('reason') != 'missing'):
            return result
        prepared = dict(preview, exif_orientation=preview['effective_orientation'],
            orientation_agrees=True, dimension_evidence=diag.get('dimensions'),
            processing_evidence=af_diagnostic._processing_evidence(metadata))
        geometry = af_diagnostic._geometry(metadata, dict(source_unchanged=True,
            metadata_source_matches=True, parser_unchanged=True), prepared)
        if geometry['status'] != 'not_validated':
            result['reason'] = geometry['reason']
            return result
        result.update(status='not_validated', reason=SONY_REFERENCE_REASON,
            center_xy_hypothesis=geometry['center_xy_hypothesis'],
            bbox_xywh_hypothesis=None,
            meaning='Uncalibrated camera playback-location reference point; no focus or subject authority',
            display_convention='A7Info A6600 embedded-preview normalized playback-point convention',
            display_evidence='independent_A7Info_documentation_and_author_linked_normalized_formula',
            limitation='old local correspondence criterion not passed; no optical or per-pixel calibration')
        return result
    if (extraction.get('profile') == 'panasonic_dc_s5m2_ifd0_jpgfromraw2_v1'
            and extraction.get('qualified_tag') == 'IFD0:JpgFromRaw2'
            and af_diagnostic._display_profile(metadata) == 'panasonic_s5m2_single_area_v1'):
        # AF values refer to the camera-rendered output convention. Do not
        # project into the 6008x4008 LibRaw buffer or invert lens coefficients.
        # Four same-shot pairs support identity framing at <3px/1600 p95 in
        # every populated 4x4 cell, including the recorded AF neighborhoods.
        raw = metadata.get('raw_tags', {})
        expected = [('PanasonicImageWidth', {'Panasonic'}, 6000),
                    ('PanasonicImageHeight', {'Panasonic'}, 4000),
                    ('CropLeft', {'IFD0'}, 4), ('CropTop', {'IFD0'}, 4),
                    ('CropRight', {'IFD0'}, 6004), ('CropBottom', {'IFD0'}, 4004),
                    ('SweepPanoramaDirection', {'Panasonic'}, 0),
                    ('SweepPanoramaFieldOfView', {'Panasonic'}, 0)]
        if _size(preview.get('decoded_source_size')) != (6000,4000):
            result['reason'] = 'panasonic_native_output_size_not_supported'
            return result
        fields = {}
        for name, groups, value in expected:
            recorded, issue = af_metadata._unique_tag(raw, name, groups)
            fields[name] = dict(value=recorded, issue=issue)
            if issue is not None or type(recorded) is not int or recorded != value:
                result['reason'] = 'panasonic_native_crop_or_dimensions_unconfirmed'
                return result
        # CIPA Exif DigitalZoomRatio numerator0 means digital zoom not used;1 is identity.
        zoom, issue = af_metadata._unique_tag(raw, 'DigitalZoomRatio', {'ExifIFD'})
        if issue is not None or isinstance(zoom,bool) or zoom not in (0,1):
            result['reason'] = 'panasonic_digital_zoom_mapping_unconfirmed'
            return result
        mode = metadata.get('diagnostics', {}).get('mode', {})
        if af_metadata._numbers(mode.get('AFAreaMode', {}).get('raw_value'),2) not in ([16.,0.],[240.,0.]):
            result['reason'] = 'panasonic_reference_area_mode_not_supported'
            return result
        prepared = dict(preview, exif_orientation=preview['effective_orientation'],
            orientation_agrees=True,
            dimension_evidence=dict(status='not_validated', fields=fields,
                reason='profiled_camera_rendered_output_not_LibRaw_frame'),
            processing_evidence=af_diagnostic._processing_evidence(metadata))
        geometry = af_diagnostic._geometry(metadata, dict(source_unchanged=True,
            metadata_source_matches=True, parser_unchanged=True), prepared)
        if geometry['status'] != 'not_validated':
            result['reason'] = geometry['reason']
            return result
        result.update(status='not_validated', reason=RAW_REASON,
            center_xy_hypothesis=geometry['center_xy_hypothesis'],
            bbox_xywh_hypothesis=geometry['bbox_xywh_hypothesis'],
            meaning='Camera search/tracking reference area, not selected person or focus success',
            display_convention='S5M2 full camera-rendered output, normalized edges then EXIF once',
            display_evidence='four_same_shot_pairs_identity_p95_under_3px_at_1600_all_16_cells',
            limitation='reference display; correspondence outliers retained; not a per-pixel error guarantee')
        return result
    if (extraction.get('profile') == 'nikon_z5_2_subifd_jpgfromraw_v1'
            and extraction.get('qualified_tag') == 'SubIFD:JpgFromRaw'
            and af_diagnostic._display_profile(metadata) == af_nikon.PROFILE):
        evidence = af_nikon.raw_native_frame_evidence(metadata['raw_tags'], preview.get('decoded_source_size'))
        if evidence['status'] != 'not_validated':
            result['reason'] = evidence['reason']
            return result
        prepared = dict(preview)
        prepared.update(exif_orientation=preview['effective_orientation'], orientation_agrees=True,
                        dimension_evidence=evidence,
                        processing_evidence=af_diagnostic._processing_evidence(metadata))
        geometry = af_diagnostic._geometry(metadata, {'source_unchanged': True,
            'metadata_source_matches': True, 'parser_unchanged': True}, prepared)
        if geometry['status'] != 'not_validated':
            result['reason'] = geometry['reason']
            return result
        result.update(status='not_validated', reason=RAW_REASON,
                      center_xy_hypothesis=geometry['center_xy_hypothesis'],
                      bbox_xywh_hypothesis=geometry['bbox_xywh_hypothesis'])
        return result
    if (extraction.get('profile') != 'canon_eos_r3_track1_jpgfromraw_v1'
            or extraction.get('qualified_tag') != 'Track1:JpgFromRaw'
            or af_diagnostic._display_profile(metadata) != af_canon.PROFILE):
        return result
    if _size(preview.get('decoded_source_size')) != (6000, 4000):
        result['reason'] = 'raw_native_frame_size_not_supported'
        return result
    # Absence is not Off. Only a valid agreeing Canon/Track4 value of zero is
    # eligible for this initial no-known-warp native-frame hypothesis.
    correction = af_canon._field(metadata.get('raw_tags', {}), 'DistortionCorrectionSetting')
    if correction['issue'] is not None or correction['value'] != 0:
        result['reason'] = 'raw_distortion_mapping_unconfirmed'
        return result
    native = {'CropLeftMargin': 0, 'CropRightMargin': 0, 'CropTopMargin': 0,
              'CropBottomMargin': 0, 'CroppedImageWidth': 6000, 'CroppedImageHeight': 4000,
              'CroppedImageLeft': 0, 'CroppedImageTop': 0, 'DigitalZoom': 0,
              'ZoomSourceWidth': 0, 'ZoomTargetWidth': 0}
    for name, expected in native.items():
        field = af_canon._field(metadata.get('raw_tags', {}), name)
        if field['issue'] is not None or field['value'] != expected:
            result['reason'] = 'raw_native_crop_or_zoom_not_established'
            return result
    prepared = dict(preview)
    prepared.update(exif_orientation=preview['effective_orientation'],
                    orientation_agrees=True,
                    dimension_evidence=af_canon.jpeg_dimension_evidence(metadata['raw_tags'], [6000, 4000]),
                    processing_evidence=af_diagnostic._processing_evidence(metadata))
    geometry = af_diagnostic._geometry(metadata, {'source_unchanged': True,
        'metadata_source_matches': True, 'parser_unchanged': True}, prepared)
    if geometry['status'] != 'not_validated':
        result['reason'] = geometry['reason']
        return result
    result.update(status='not_validated', reason=RAW_REASON,
                  center_xy_hypothesis=geometry['center_xy_hypothesis'],
                  bbox_xywh_hypothesis=geometry['bbox_xywh_hypothesis'])
    if geometry.get('records'):
        result['records'] = geometry['records']
    return result


def _status(reason: str, viewable: bool) -> str:
    if reason == 'record_collection_exceeds_display_limit':
        return 'RAW内嵌预览可查看；AF超过4096条显示上限，不截断绘制'
    if reason == RAW_REASON:
        return 'AF：RAW内嵌预览记录点（未校准，非清晰度证明）'
    if viewable:
        return 'RAW内嵌预览可查看；AF坐标映射未确认，暂不绘制'
    labels = {'exiftool_unavailable': '未找到可选ExifTool',
              'unsupported_camera': '此相机的内嵌预览尚未支持',
              'source_unavailable': '原件无法读取'}
    return 'RAW预览不可用：' + labels.get(reason, reason)


def bind_raw_preview(path: str, image: Image.Image | None, report: dict[str, Any],
                     *, max_edge: int = 768) -> _PreparedBundle:
    """Worker-only binding; check actual source/parser and all snapshot links."""
    try:
        if not isinstance(report, dict) or report.get('schema') != 'af-raw-source-v1':
            raise ValueError('invalid_raw_prepared_report')
        metadata = deepcopy(report.get('metadata') or {})
        source = deepcopy(report.get('source') or {})
        binding = deepcopy(report.get('binding') or {})
        preview = deepcopy(report.get('preview') or {})
        extraction = deepcopy(report.get('extraction') or {})
        snapshot = deepcopy(report.get('snapshot') or {})
        provenance = {'source': source, 'binding': binding, 'preview': preview,
                      'extraction': extraction, 'snapshot': snapshot,
                      'orientation': deepcopy(report.get('orientation') or {}),
                      'parser': deepcopy(metadata.get('parser')),
                      'route': 'same_RAW_private_snapshot_embedded_JPEG'}
        if report.get('viewable_preview') is not True:
            if image is not None:
                image.close()
            reason = str(report.get('reason') or 'raw_preview_unavailable')
            return _PreparedBundle(metadata=metadata, clean_image=None, request_path=path,
                provenance=provenance, geometry={'status': 'unknown', 'reason': reason},
                status='unavailable', reason=reason, status_text=_status(reason, False))
        canonical = Path(path).expanduser().resolve(strict=True)
        if (canonical.suffix.lower() not in RAW_SUFFIXES
                or source.get('path') != str(canonical)
                or _source_key(source) is None
                or _source_key(af_metadata._source_identity(canonical)) != _source_key(source)
                or source.get('unchanged_during_read') is not True
                or any(binding.get(key) is not True for key in
                       ('source_unchanged', 'snapshot_matches_source', 'snapshot_unchanged', 'metadata_matches_snapshot', 'parser_unchanged'))):
            raise ValueError('raw_source_changed_or_unbound')
        if (snapshot.get('sha256') != source['sha256'] or _source_key(snapshot) is None
                or snapshot.get('removed') is not True
                or snapshot.get('path') == source.get('path')
                or snapshot.get('kind') != 'private_read_only_copy'
                or snapshot.get('stat_identity', {}).get('size') != source['stat_identity']['size']
                or _source_key(metadata.get('source')) != _source_key(snapshot)
                or metadata['source'].get('unchanged_during_read') is not True
                or metadata.get('raw_tags', {}).get('SourceFile') != snapshot['path']):
            raise ValueError('raw_snapshot_metadata_unbound')
        parser = metadata.get('parser')
        if (_parser_key(parser) is None or not af_diagnostic._parser_unchanged(parser)
                or parser.get('adapter_version') != af_metadata.ADAPTER_VERSION
                or extraction.get('parser', {}).get('path') != parser['path']
                or extraction.get('parser', {}).get('version') != parser['version']
                or extraction.get('parser', {}).get('stat_identity') != parser['stat_identity']
                or extraction.get('parser', {}).get('unchanged_during_read') is not True
                or extraction.get('parser', {}).get('sha256') != af_metadata._source_identity(Path(parser['path']))['sha256']):
            raise ValueError('raw_parser_changed_or_unbound')
        if (report.get('status') != 'viewable'
                or report.get('usable_for_subject_selection') is not False
                or report.get('usable_for_keep_reject') is not False
                or metadata.get('usable_for_subject_selection') is not False
                or metadata.get('usable_for_keep_reject') is not False
                or report.get('af_geometry_supported') is not False):
            raise ValueError('raw_automatic_decisions_not_disabled')
        if metadata.get('diagnostics') != af_metadata._diagnostics(metadata.get('raw_tags', {})):
            raise ValueError('raw_diagnostics_inconsistent')
        from .af_raw_source import _profile
        try:
            expected_profile = _profile(metadata['raw_tags'], canonical.suffix.lower())
        except Exception as exc:
            raise ValueError('raw_extraction_profile_unbound') from exc
        if (extraction.get('profile') != expected_profile.name
                or extraction.get('group') != expected_profile.group
                or extraction.get('tag') != expected_profile.tag
                or extraction.get('qualified_tag') != expected_profile.qualified_tag):
            raise ValueError('raw_extraction_profile_unbound')
        orientation = preview.get('effective_orientation')
        original_orientation = provenance['orientation'].get('raw_orientation')
        embedded_orientation = preview.get('embedded_orientation')
        raw_size, frame_size = _size(preview.get('decoded_source_size')), _size(preview.get('display_frame_size'))
        if (type(orientation) is not int or orientation not in range(1, 9)
                or type(original_orientation) is not int or original_orientation != orientation
                or (embedded_orientation is not None and
                    (type(embedded_orientation) is not int or embedded_orientation != orientation))
                or raw_size is None or frame_size != (raw_size[::-1] if orientation >= 5 else raw_size)
                or preview.get('exif_transposed') is not True
                or type(preview.get('exif_transpose_count')) is not int or preview.get('exif_transpose_count') != 1
                or preview.get('crop') != 'full_frame' or preview.get('annotation_applied') is not False):
            raise ValueError('raw_preview_orientation_or_frame_unbound')
        if (not isinstance(image, Image.Image) or image.mode != 'RGB'
                or image.size != _size(preview.get('raster_size')) or max(image.size) > max_edge
                or preview.get('pixel_mode') != 'RGB'
                or preview.get('decoded_bytes_sha256') != extraction.get('encoded_jpeg_sha256')
                or type(extraction.get('encoded_jpeg_bytes')) is not int
                or not 0 < extraction['encoded_jpeg_bytes'] <= 64 * 1024 * 1024
                or not isinstance(extraction.get('encoded_jpeg_sha256'), str)
                or len(extraction['encoded_jpeg_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in extraction['encoded_jpeg_sha256'])
                or min(abs(image.width-image.height*frame_size[0]/frame_size[1]),
                       abs(image.height-image.width*frame_size[1]/frame_size[0])) > 1):
            raise ValueError('raw_preview_pixels_or_frame_unbound')
        pixels = hashlib.sha256(image.tobytes()).hexdigest()
        if pixels != preview.get('clean_pixels_sha256'):
            raise ValueError('raw_preview_pixels_changed')
        geometry = _geometry(metadata, report)
        reason = geometry['reason']
        bundle = _PreparedBundle(metadata=metadata, clean_image=image, provenance=provenance,
            geometry=geometry, request_path=path,
            status='diagnostic' if geometry['status'] == 'not_validated' else 'viewable',
            reason=reason, status_text=_status(reason, True))
        if reason == SONY_REFERENCE_REASON:
            bundle['status_text'] = 'AF回放参考点（未校准）：仅供位置参考，不代表已合焦人物，不用于自动选人或清晰度。'
        elif geometry.get('display_convention'):
            bundle['status_text'] = 'AF参考区域：相机搜索/跟踪范围（未校准；不代表已合焦人物）'
        if len(geometry.get('records', [])) > 1:
            bundle['status_text'] = ('AF：RAW内嵌预览多点；长虚线=选中，点线=机报合焦，'
                                   '点划线=两者（非清晰度证明）')
        if geometry['status'] == 'not_validated':
            bundle._pixel_binding = _PixelBinding(image, image.size, pixels, _fingerprint(bundle),
                _numbers(geometry['center_xy_hypothesis'], 2),
                _numbers(geometry.get('bbox_xywh_hypothesis'), 4), reason, _sealed_markers(geometry))
        return bundle
    except Exception:
        if image is not None:
            image.close()
        raise
