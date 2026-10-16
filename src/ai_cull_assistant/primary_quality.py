"""Confirmed-primary-subject quality policy; no settings or decision mutation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from numbers import Real

from . import image_decode
from .crop_settings import face_box_key

POLICY_VERSION = "confirmed-primary-quality-v1"
CONFIRMATION_KEY = "primary_subject_confirmation"

_CROP_KEYS = (
    "scale_factor", "shift_factor", "offset_x_factor",
    "aspect_ratio", "detection_confidence",
)
_ENTRY_KEYS = _CROP_KEYS + (
    "selected_faces", "manual_face", "selected_bodies",
    "hidden", "subjects_disabled", "face_crops",
    "roi_selection_schema",
    "primary_face_index", "primary_body_index",
    "primary_face_id", "primary_body_id",
    "primary_face_basis", "primary_body_basis",
)
_SCALE_KEYS = (
    "contrast", "laplacian_normalized", "gradient_p90_normalized",
    "edge_curvature", "orientation_coherence",
)
_BODY_METRIC_KEYS = (
    "contrast", "texture_fraction", "laplacian_normalized",
    "coarse_laplacian_normalized", "edge_curvature", "edge_anisotropy",
)
_BODY_FAMILIES = {
    "torso": "torso",
    "left_upper_arm": "left_arm",
    "left_lower_arm": "left_arm",
    "right_upper_arm": "right_arm",
    "right_lower_arm": "right_arm",
    "left_upper_leg": "left_leg",
    "left_lower_leg": "left_leg",
    "right_upper_leg": "right_leg",
    "right_lower_leg": "right_leg",
}
_FACE_BLOCKERS = {
    "face_too_small_for_focus",
    "missing_reliable_landmarks",
    "insufficient_native_eye_pixels",
    "insufficient_local_contrast",
    "high_frequency_noise_or_artifacts",
    "source_detail_evidence_insufficient",
}


def _number(value):
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _digest(value):
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Expected normalized xywh")
    if not all(_number(v) for v in value):
        raise ValueError("Box coordinates must be finite numbers, not booleans")
    x, y, w, h = [0.0 if float(v) == 0 else float(v) for v in value]
    # This is the existing ROI-contract geometry tolerance.
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        raise ValueError("Box outside image")
    return [x, y, w, h]


def _same_box(left, right):
    try:
        return _box(left) == _box(right)
    except (TypeError, ValueError):
        return False


def _entry(asset, settings):
    entry = settings.photos.get(settings.key(asset), {})
    if not isinstance(entry, dict):
        raise ValueError("Photo settings must be an object")
    if any(key in entry and type(entry[key]) is not bool for key in ('hidden','subjects_disabled')):
        raise ValueError('Invalid selection visibility')
    return entry


def selection_regions(entry):
    """Return only manually selected regions.

    Invalid selected entries invalidate the whole selection; they are not
    silently dropped. Explicit [] overrides legacy manual_face.
    """
    if not isinstance(entry, dict):
        raise ValueError("Photo settings must be an object")
    if any(key in entry and type(entry[key]) is not bool for key in ('hidden','subjects_disabled')):
        raise ValueError('Invalid selection visibility')
    if entry.get("hidden") or entry.get("subjects_disabled"):
        return []

    groups = []
    if "selected_faces" in entry:
        groups.append(("face", entry["selected_faces"]))
    elif entry.get("manual_face") is not None:
        groups.append(("face", [entry["manual_face"]]))
    if "selected_bodies" in entry:
        groups.append(("body", entry["selected_bodies"]))

    output, seen = [], {}
    for kind, items in groups:
        if not isinstance(items, list):
            raise ValueError("Selected regions must be a list")
        for item in items:
            if isinstance(item, dict):
                if any(key in item and type(item[key]) is not bool for key in ('hidden','subjects_disabled')):
                    raise ValueError('Invalid region visibility')
                if item.get("hidden") or item.get("subjects_disabled"):
                    continue
                item = item.get("box")
            box = _box(item)
            identity = kind + ":" + face_box_key(box)
            previous = seen.get(identity)
            if previous is not None:
                if previous != box:
                    # Existing six-decimal identifiers must not merge two ROIs.
                    raise ValueError("Ambiguous selected-region identifier")
                continue
            seen[identity] = box
            output.append({"id": identity, "kind": kind, "box": box})
    return sorted(output, key=lambda item: item["id"])


def confirm_selection(asset, settings):
    """Build metadata only. Caller saves this after an explicit human action."""
    from .body_focus import VERSION as body_version
    from .face_focus import VERSION as face_version
    from .roi_contract import SCHEMA_VERSION
    from .subject import DETECTION_VERSION

    entry = _entry(asset, settings)
    regions = selection_regions(entry)
    if not regions:
        raise ValueError("No manually selected primary subjects")

    source = _canonical(image_decode.asset_source_signature(asset))
    if not source or any(
        not isinstance(item, list)
        or len(item) != 2
        or not isinstance(item[1], list)
        or len(item[1]) < 2
        or item[1][1] is None
        for item in source
    ):
        raise ValueError("Source identity unavailable")

    parameters = _canonical({
        "global": {key: getattr(settings, key) for key in _CROP_KEYS},
        "photo": {key: entry[key] for key in _ENTRY_KEYS if key in entry},
        "versions": {
            "roi": SCHEMA_VERSION,
            "detection": DETECTION_VERSION,
            "face": face_version,
            "body": body_version,
        },
    })
    result = {
        "version": POLICY_VERSION,
        "human_confirmed": True,
        "source_signature": source,
        "parameters_digest": _digest(parameters),
        "regions": regions,
    }
    result["digest"] = _digest(result)
    return result


def confirmed_selection(asset, settings):
    try:
        saved = _entry(asset, settings).get(CONFIRMATION_KEY)
        if not isinstance(saved, dict):
            return None
        current = confirm_selection(asset, settings)
        return current if _canonical(saved) == current else None
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return None


def _part(part, state, reasons, *, reliable=False):
    return {
        "part": part,
        "state": state,
        "reliability": "adequate" if reliable else "insufficient",
        "reasons": list(reasons),
    }


def _finite_metrics(value, keys):
    return (
        isinstance(value, dict)
        and all(_number(value.get(k)) and value[k] >= 0 for k in keys)
    )


def _face_part(item):
    """Validate the existing face decision and its necessary measurements."""
    from .focus_metrics import MIN_NATIVE_EYE_DISTANCE

    unknown = _part("face", "uncertain", ["face_measurement_not_supported"])
    if not isinstance(item, dict):
        return _part("face", "unassessed", ["face_evidence_missing"])
    reasons = item.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        return unknown
    if _FACE_BLOCKERS.intersection(reasons):
        return unknown
    if (
        item.get("landmarks_used") is not True
        or not _number(item.get("native_face_width"))
        or item["native_face_width"] < 96
        or not _number(item.get("native_eye_distance"))
        or item["native_eye_distance"] < MIN_NATIVE_EYE_DISTANCE
    ):
        return unknown

    regions = item.get("regions")
    if not isinstance(regions, dict):
        return unknown
    eye, core = regions.get("eye_band"), regions.get("face_core")
    for region in (eye, core):
        if not isinstance(region, dict):
            return unknown
        if not all(
            _finite_metrics(region.get(key), _SCALE_KEYS)
            for key in ("fine", "coarse", "native_fine", "native_coarse")
        ):
            return unknown
        if not _finite_metrics(
            region, ("resize_scale", "fine_to_coarse_ratio", "gradient_retention")
        ):
            return unknown
        size = region.get("native_size")
        if (
            not isinstance(size, (list, tuple))
            or len(size) != 2
            or not all(_number(x) and x > 0 for x in size)
            or region["resize_scale"] <= 0
        ):
            return unknown

    if item.get("fine") != eye["fine"] or item.get("coarse") != eye["coarse"]:
        return unknown

    ef, ec, cf = eye["fine"], eye["coarse"], core["fine"]
    if (
        ef["contrast"] < .10 or cf["contrast"] < .08
        or eye["fine_to_coarse_ratio"] > 12.0
        or core["fine_to_coarse_ratio"] > 14.0
    ):
        return unknown

    severe = (
        ef["laplacian_normalized"] < .00145
        and cf["laplacian_normalized"] < .00175
        and ef["edge_curvature"] < .16
    ) or (
        ef["laplacian_normalized"] < .0032
        and cf["laplacian_normalized"] < .0034
        and ef["edge_curvature"] < .15
        and ec["laplacian_normalized"] < .0010
    )
    directional = (
        ef["orientation_coherence"] > .72
        and eye["gradient_retention"] > .68
        and ef["edge_curvature"] < .19
    )
    clear = (
        not severe and not directional
        and ef["laplacian_normalized"] >= .0045
        and cf["laplacian_normalized"] >= .0060
        and ef["edge_curvature"] >= .18
        and eye["native_fine"]["edge_curvature"] >= .215
        and ef["gradient_p90_normalized"] >= .25
        and ec["laplacian_normalized"] >= .0008
    )
    state = item.get("face_measurement_state")
    if state == "severe_blur" and severe and "weak_eye_and_face_detail" in reasons:
        return _part("face", state, ["weak_eye_and_face_detail"], reliable=True)
    if state == "clear" and clear and "strong_landmark_aligned_detail" in reasons:
        return _part("face", state, ["strong_landmark_aligned_detail"], reliable=True)
    return _part("face", "uncertain", reasons or ["face_quality_uncertain"])


def _body_part(item, *, face_box=None, linked=False):
    from .body_focus import _aggregate_regions, _headshot

    if not isinstance(item, dict):
        return _part("body", "unassessed", ["body_evidence_missing"])

    reasons = item.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        return _part("body", "uncertain", ["invalid_body_evidence"])
    association = item.get('association')
    associated_face = item.get('associated_face')
    if associated_face is not None and (type(associated_face) is not int or associated_face <= 0):
        return _part('body','uncertain',['invalid_body_association'])
    if association is not None and not isinstance(association,str):
        return _part('body','uncertain',['invalid_body_association'])
    if association in {"ambiguous", "unverified", "reverse_conflict"}:
        return _part("body", "uncertain", ["body_association_unresolved"])

    # This means no visible body claim, never "body clear".
    if (
        linked and face_box is not None
        and item.get("state") == "clear"
        and reasons == ["body_not_visible_headshot"]
        and not item.get("regions")
        and _headshot(tuple(face_box))
    ):
        return _part("body", "not_visible", reasons, reliable=True)

    diagnostics = item.get("diagnostics")
    if linked and isinstance(diagnostics, dict):
        confidence_ok = (
            _number(diagnostics.get("person_confidence"))
            and .5 <= diagnostics["person_confidence"] <= 1
            and _number(diagnostics.get("pose_confidence"))
            and .5 <= diagnostics["pose_confidence"] <= 1
            and diagnostics.get("ambiguous") is not True
        )
    else:
        confidence_ok = (
            _number(item.get("confidence"))
            and .55 <= item["confidence"] <= 1
        )
    if not confidence_ok:
        return _part("body", "uncertain", ["body_confidence_not_supported"])

    regions = item.get("regions")
    if not isinstance(regions, list) or not regions:
        return _part("body", "unassessed", ["body_regions_missing"])

    checked, names = [], set()
    for region in regions:
        if not isinstance(region, dict):
            return _part("body", "uncertain", ["invalid_body_region"])
        name = region.get("name")
        if (
            not isinstance(name,str) or name not in _BODY_FAMILIES
            or region.get("family") != _BODY_FAMILIES[name]
            or name in names
        ):
            return _part("body", "uncertain", ["invalid_body_region_identity"])
        names.add(name)
        box, measured = region.get("box"), region.get("evidence")
        if (
            not isinstance(box, (list, tuple)) or len(box) != 4
            or not all(_number(x) for x in box)
            or box[0] < 0 or box[1] < 0
            or box[2] <= box[0] or box[3] <= box[1]
            or not isinstance(measured, dict)
        ):
            return _part("body", "uncertain", ["invalid_body_region"])

        if measured.get("state") == "insufficient":
            checked.append(deepcopy(region))
            continue
        if not _finite_metrics(measured, _BODY_METRIC_KEYS):
            return _part("body", "uncertain", ["invalid_body_metrics"])
        if measured["contrast"] > 1 or measured["texture_fraction"] > 1:
            return _part("body", "uncertain", ["invalid_body_metrics"])

        if measured["contrast"] < .10 or measured["texture_fraction"] < .08:
            state, reason = "insufficient", "low_texture_or_contrast"
        elif (
            measured["laplacian_normalized"] < .0011
            and measured["coarse_laplacian_normalized"] < .00042
            and measured["edge_curvature"] < .115
        ):
            state, reason = "severe_blur", "weak_detail_at_both_scales"
        elif (
            measured["laplacian_normalized"] >= .0038
            and measured["coarse_laplacian_normalized"] >= .00072
            and measured["edge_curvature"] >= .16
        ):
            state, reason = "clear", "strong_consistent_detail"
        else:
            state, reason = "uncertain", "borderline_body_detail"
        if measured.get("state") != state or measured.get("reason") != reason:
            return _part("body", "uncertain", ["body_metrics_state_disagree"])
        checked.append(deepcopy(region))

    state, aggregate_reasons = _aggregate_regions(checked)
    if item.get("state") != state or not set(aggregate_reasons).issubset(reasons):
        return _part("body", "uncertain", ["body_aggregate_not_supported"])
    result = _part(
        "body", state, aggregate_reasons,
        reliable=state in {"clear", "severe_blur"},
    )
    result["regions"] = checked
    result["cause"] = "unassessed"
    return result


def _body_records(evidence, subjects):
    # _portrait_result exposes the complete authoritative body list here.
    if "body_results" in evidence:
        records = evidence["body_results"]
        return records if isinstance(records, list) else []
    # Compatibility for callers that supply only subjects.
    records = [s for s in subjects if s.get("evidence_kind") == "body"]
    for face in subjects:
        attached = face.get("body_evidence")
        if isinstance(attached, list):
            records.extend(x for x in attached if isinstance(x, dict))
    return records


def _policy_payload(confirmation, subjects, issues, raw_subjects):
    severe = any(
        part["state"] == "severe_blur" and part["reliability"] == "adequate"
        for subject in subjects for part in subject["parts"]
    )
    clear = bool(subjects) and all(
        subject["parts"]
        and all(
            part["state"] in {"clear", "not_visible"}
            and part["reliability"] == "adequate"
            for part in subject["parts"]
        )
        for subject in subjects
    )
    recommendation = "reject" if severe else "keep" if clear else "review"
    return {
        "version": POLICY_VERSION,
        "confirmation_digest": confirmation["digest"] if confirmation else None,
        "recommendation": recommendation,
        "state": {
            "reject": "severe_blur", "keep": "clear", "review": "uncertain",
        }[recommendation],
        "subjects": subjects,
        "issues": issues,
        "original_subjects": deepcopy(raw_subjects),
    }


def evaluate_local_evidence(asset, settings, evidence):
    """Return a separate policy payload; never overwrite a manual decision."""
    evidence = evidence if isinstance(evidence, dict) else {}
    raw_subjects = evidence.get("subjects")
    raw_subjects = raw_subjects if isinstance(raw_subjects, list) else []
    subjects = [s for s in raw_subjects if isinstance(s, dict)]
    confirmation = confirmed_selection(asset, settings)

    if confirmation is None:
        return _policy_payload(None, [], [{
            "subject_id": None, "part": "selection",
            "state": "uncertain", "reason": "primary_selection_unconfirmed",
        }], raw_subjects)
    if evidence.get("measurement_confirmation_digest") != confirmation["digest"]:
        return _policy_payload(confirmation, [], [{
            "subject_id": None, "part": "measurement",
            "state": "uncertain", "reason": "measurement_confirmation_mismatch",
        }], raw_subjects)

    faces = [s for s in subjects if s.get("evidence_kind") == "face"]
    bodies = [
        b for b in _body_records(evidence, subjects)
        if isinstance(b, dict)
    ]
    selected_faces = [r for r in confirmation["regions"] if r["kind"] == "face"]
    selected_bodies = [r for r in confirmation["regions"] if r["kind"] == "body"]
    output, issues, consumed_bodies = [], [], set()

    for region in selected_faces:
        matches = [
            f for f in faces
            if _same_box(f.get("normalized_box"), region["box"])
        ]
        face = matches[0] if len(matches) == 1 else None
        parts = [_face_part(face)]
        own_body = face.get("body") if face else None

        participant = face.get("participant") if face else None
        valid_participant = (
            isinstance(participant, int) and not isinstance(participant, bool)
            and participant > 0
            and sum(f.get("participant") == participant for f in faces) == 1
        )
        attached = [
            (i, b) for i, b in enumerate(bodies)
            if valid_participant and type(b.get('associated_face')) is int
            and b.get("associated_face") == participant
        ]
        if len(attached) == 1 and attached[0][1].get("association") == "pose_head_overlap":
            index, body = attached[0]
            consumed_bodies.add(index)
            parts.append(_body_part(body))
            body_ids = [
                r["id"] for r in selected_bodies
                if _same_box(body.get("box"), r["box"])
            ]
        elif attached:
            parts.append(_part("body", "uncertain", ["body_association_unresolved"]))
            body_ids = []
        elif isinstance(own_body, dict):
            parts.append(_body_part(
                own_body, face_box=region["box"], linked=True,
            ))
            body_ids = []
        else:
            parts.append(_part("body", "unassessed", ["body_evidence_missing"]))
            body_ids = []

        output.append({
            "original_body_evidence": deepcopy([b for _,b in attached]),
            "id": region["id"],
            "region_ids": [region["id"], *body_ids],
            "kind": "person",
            "parts": parts,
            "original_evidence": deepcopy(face),
        })

    represented = {rid for item in output for rid in item["region_ids"]}
    for region in selected_bodies:
        if region["id"] in represented:
            continue
        matches = [
            (i, b) for i, b in enumerate(bodies)
            if i not in consumed_bodies and _same_box(b.get("box"), region["box"])
        ]
        body = matches[0][1] if len(matches) == 1 else None
        parts = [
            _body_part(body),
            _part("face", "unassessed", ["face_and_eyes_unassessed"]),
        ]
        output.append({
            "id": region["id"],
            "region_ids": [region["id"]],
            "kind": "person",
            "parts": parts,
            "original_evidence": deepcopy(body),
        })

    for subject in output:
        for part in subject["parts"]:
            if part["state"] in {"clear", "not_visible"}:
                continue
            issues.append({
                "subject_id": subject["id"],
                "part": part["part"],
                "state": part["state"],
                "reliability": part["reliability"],
                "reasons": deepcopy(part["reasons"]),
                "regions": deepcopy(part.get("regions", [])),
            })

    # A source/settings change during evaluation must invalidate the result.
    rechecked = confirmed_selection(asset, settings)
    if rechecked is None or rechecked["digest"] != confirmation["digest"]:
        return _policy_payload(None, [], [{
            "subject_id": None, "part": "measurement",
            "state": "uncertain", "reason": "inputs_changed_during_evaluation",
        }], raw_subjects)
    return _policy_payload(confirmation, output, issues, raw_subjects)
