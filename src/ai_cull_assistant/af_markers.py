"""Finite, display-only camera record collections; no subject/quality authority."""
from __future__ import annotations
import math
from . import af_metadata


def canon_records(metadata: dict, orientation: int) -> list[dict] | None:
    """Recompute and validate the complete Canon record collection, or reject it."""
    from . import af_canon
    raw = metadata.get('raw_tags', {})
    diagnostic = metadata.get('diagnostics', {})
    if not af_canon.matches_camera(raw):
        return None
    supplied = diagnostic.get('coordinates', {}).get('display_records')
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 4096:
        return None
    if af_canon.diagnostics(raw) != diagnostic:
        return None
    mode, coordinates = diagnostic.get('mode', {}), diagnostic.get('coordinates', {})
    if (mode.get('status') != 'not_validated' or mode.get('display_records_candidate') is not True
            or coordinates.get('status') != 'not_validated'
            or type(orientation) is not int or orientation not in range(1, 9)
            or diagnostic.get('orientation', {}).get('exif_value') != orientation):
        return None
    records = coordinates.get('display_records')
    if not isinstance(records, list) or not 1 <= len(records) <= 4096:
        return None
    result = []
    indices = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or type(record.get('index')) is not int:
            return None
        index = record['index']
        if index < 0 or index in seen:
            return None
        indices.append(index)
        seen.add(index)
        selected, focused = record.get('selected'), record.get('camera_reported_in_focus')
        if any(value is not None and type(value) is not bool for value in (selected, focused)):
            return None
        if selected is not True and focused is not True:
            return None
        role = ('selected_and_camera_reported_in_focus' if selected and focused else
                'camera_reported_in_focus' if focused else 'selected')
        if record.get('display_role') != role:
            return None
        try:
            center = af_metadata.orient_normalized_point_hypothesis(record['center_xy'], orientation)
            box = af_metadata.orient_normalized_box_hypothesis(record['raw_bbox_xywh_hypothesis'], orientation)
            if center != record.get('oriented_center_xy_hypothesis') or box != record.get('oriented_bbox_xywh_hypothesis'):
                return None
            if not all(type(x) in (float, int) and math.isfinite(x) for x in (*center, *box)):
                return None
            x, y, w, h = box
            if (x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1 or y+h > 1
                    or not math.isclose(x+w/2, center[0], abs_tol=1e-12)
                    or not math.isclose(y+h/2, center[1], abs_tol=1e-12)):
                return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        result.append({'index': index, 'role': role, 'selected': selected,
                       'camera_reported_in_focus': focused,
                       'center_xy_hypothesis': center, 'bbox_xywh_hypothesis': box})
    if indices != coordinates.get('active_point_indices'):
        return None
    return result
