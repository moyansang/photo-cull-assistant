"""Source-bound primary-subject quality policy; no settings or decision mutation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from numbers import Real

from . import image_decode
from .crop_settings import face_box_key

POLICY_VERSION = "confirmed-primary-quality-v2-effective-region"
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
    "face_visibility_unverified",
    "face_region_outside_source",
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



def measurement_inputs(asset, settings, enabled_parts):
    """Snapshot source, effective settings and requested parts before measuring."""
    from .body_focus import VERSION as body_version
    from .face_focus import VERSION as face_version
    from .roi_contract import SCHEMA_VERSION
    from .subject import DETECTION_VERSION
    if enabled_parts not in (["face"], ["face", "body"]):
        raise ValueError("Invalid enabled inspection parts")
    source = _canonical(image_decode.asset_source_signature(asset))
    if not source or any(not isinstance(item, list) or len(item) != 2
                         or not isinstance(item[1], list) or len(item[1]) < 2
                         or item[1][1] is None for item in source):
        raise ValueError("Source identity unavailable")
    entry = _entry(asset, settings)
    return {
        "source_signature": source,
        "parameters_digest": _digest({
            "global": {key: getattr(settings, key) for key in _CROP_KEYS},
            "photo": {key: entry[key] for key in _ENTRY_KEYS if key in entry},
            "confirmation": entry.get(CONFIRMATION_KEY),
            "versions": [SCHEMA_VERSION, DETECTION_VERSION, face_version, body_version],
            "enabled_parts": enabled_parts,
        }),
        "enabled_parts": list(enabled_parts),
    }


def automatic_selection(asset, settings, evidence, inputs):
    """Build an automatic range from actual detector provenance, never a consent."""
    entry = _entry(asset, settings)
    # A changed or unconfirmed manual selection must never silently become auto.
    if any(key in entry for key in ("selected_faces", "manual_face", "selected_bodies", CONFIRMATION_KEY)):
        return None
    if entry.get("hidden") or entry.get("subjects_disabled") or evidence.get("selection_pending"):
        return None
    proof = evidence.get("automatic_subject")
    if not isinstance(proof, dict) or proof.get("version") != "single-detected-face-v1":
        return None
    if type(proof.get("candidate_count")) is not int or proof["candidate_count"] != 1:
        return None
    # New measured records carry all detector candidates. A rescued face must
    # not inherit the initial detector's single-candidate count.
    provenance = proof.get("candidate_provenance")
    if provenance is not None:
        if not isinstance(provenance, dict):
            return None
        candidates = provenance.get("initial_candidates")
        if (provenance.get("diagnostic_version") != "subject-candidates-v1"
                or provenance.get("coordinate_space") != "normalized_exif_oriented"
                or not isinstance(candidates, (list, tuple)) or len(candidates) != 1
                or type(provenance.get("chosen_initial_index")) is not int
                or provenance["chosen_initial_index"] != 0
                or provenance.get("chosen_source") != "initial_yunet"
                or not isinstance(candidates[0], dict)
                or not _same_box(candidates[0].get("box"), proof.get("box"))
                or candidates[0].get("landmarks_available") is not True):
            return None
    # The threshold is a detector setting, NOT the selected face's confidence.
    if proof.get("detection_threshold") != settings.detection_confidence:
        return None
    if proof.get("landmarks_available") is not True:
        return None
    box = _box(proof.get("box"))
    faces = [item for item in evidence.get("subjects", [])
             if isinstance(item, dict) and item.get("evidence_kind") == "face"]
    if len(faces) != 1 or not _same_box(faces[0].get("normalized_box"), box):
        return None
    if measurement_inputs(asset, settings, inputs["enabled_parts"]) != inputs:
        return None
    result = dict(inputs, version="automatic-primary-selection-v1",
                  origin="automatic", human_confirmed=False,
                  regions=[{"id": "face:" + face_box_key(box), "kind": "face", "box": box}],
                  detector_provenance=_canonical(proof))
    result["digest"] = _digest(result)
    return result


def current_selection(asset, settings, evidence=None):
    """Current human or measured automatic range; human confirmation stays separate."""
    evidence = (getattr(asset, "clarity_evidence", None) or {}) if evidence is None else evidence
    if not isinstance(evidence, dict):
        return None
    confirmation = confirmed_selection(asset, settings)
    if confirmation is not None:
        inputs = evidence.get("measurement_inputs")
        if inputs is not None:
            try:
                if measurement_inputs(asset, settings, inputs["enabled_parts"]) != inputs:
                    return None
                confirmation = dict(confirmation, origin="human", enabled_parts=inputs["enabled_parts"])
            except (KeyError, AttributeError, OSError, TypeError, ValueError):
                return None
        return confirmation
    try:
        saved = evidence.get("automatic_selection")
        if not isinstance(saved, dict):
            return None
        current = automatic_selection(asset, settings, evidence, evidence["measurement_inputs"])
        if (current is not None and current == saved
                and evidence.get("measurement_selection_digest") == current["digest"]):
            return current
    except (KeyError, AttributeError, OSError, TypeError, ValueError, OverflowError):
        pass
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


def face_reasons(item):
    """Read part-specific reasons; legacy originals remain untouched for audit."""
    if "face_reasons" in item:
        return item["face_reasons"]
    reasons = item.get("reasons")
    if not isinstance(reasons, list):
        return reasons
    body = item.get("body")
    def body_reason_codes(record):
        raw = record.get("reasons") if isinstance(record, dict) else None
        return {code for code in raw if isinstance(code, str)} if isinstance(raw, list) else set()
    body_codes = body_reason_codes(body)
    for attached in item.get("body_evidence", []) if isinstance(item.get("body_evidence"), list) else []:
        if isinstance(attached, dict):
            body_codes.update(body_reason_codes(attached))
    return [r for r in reasons if isinstance(r, str) and r not in body_codes
            and not r.startswith(("body_", "multiple_major_body_", "isolated_body_"))
            and r not in {"face_body_quality_disagree", "ambiguous_face_body_association"}]


def _face_part(item):
    """Validate the existing face decision and its necessary measurements."""
    from .focus_metrics import MIN_NATIVE_EYE_DISTANCE

    unknown = _part("face", "uncertain", ["face_measurement_not_supported"])
    if not isinstance(item, dict):
        return _part("face", "unassessed", ["face_evidence_missing"])
    reasons = face_reasons(item)
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        return unknown
    if _FACE_BLOCKERS.intersection(reasons):
        return _part("face", "uncertain", reasons)
    if (
        item.get("landmarks_used") is not True
        or not _number(item.get("native_face_width"))
        or item["native_face_width"] < 96
        or not _number(item.get("native_eye_distance"))
        or item["native_eye_distance"] < MIN_NATIVE_EYE_DISTANCE
    ):
        return unknown

    regions = item.get("regions")
    if isinstance(regions, dict) and any(
        isinstance(region, dict) and isinstance(region.get("effective_area"), dict)
        and region["effective_area"].get("source_supported") is not True
        for region in regions.values()
    ):
        return _part("face", "uncertain", ["face_region_outside_source"])
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


def _body_region_coordinates_supported(region):
    if region.get("version") != "body-focus-v2" or region.get("coordinate_space") != "exif_transposed_image_pixels_xyxy":
        return False
    size = region.get("coordinate_image_size")
    measured_size = region.get("measurement_image_size")
    origin = region.get("measurement_origin_px")
    box = region.get("box")
    measured_box = region.get("measurement_box")
    for values, length in ((size, 2), (measured_size, 2), (origin, 2), (box, 4), (measured_box, 4)):
        if not isinstance(values, (list, tuple)) or len(values) != length or not all(_number(v) for v in values):
            return False
    if min(*size, *measured_size) <= 0 or min(*origin) < 0:
        return False
    if not (0 <= measured_box[0] < measured_box[2] <= measured_size[0]
            and 0 <= measured_box[1] < measured_box[3] <= measured_size[1]
            and 0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1]):
        return False
    return all(box[i] == measured_box[i] + origin[i % 2] for i in range(4))


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
        return _part("body", "uncertain", list(dict.fromkeys(reasons + ["body_confidence_not_supported"])))

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

        if item.get("version") == "body-focus-v2":
            if not _body_region_coordinates_supported(region):
                return _part("body", "uncertain", ["body_region_coordinates_unverified"])
            if not isinstance(region.get("ownership"), dict):
                return _part("body", "uncertain", ["body_region_ownership_unresolved"])

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
    by_part = {name: [] for name in ("selection", "face", "body", "measurement")}
    for issue in issues:
        part = issue.get("part", "measurement")
        codes = issue.get("reasons", [issue["reason"]] if "reason" in issue else [])
        by_part.setdefault(part, []).extend(code for code in codes if code not in by_part.get(part, []))
    return {
        "reason_schema_version": "part-reasons-v1",
        "reasons_by_part": by_part,
        "version": POLICY_VERSION,
        "confirmation_digest": confirmation["digest"] if confirmation and confirmation.get("human_confirmed") else None,
        "selection_digest": confirmation["digest"] if confirmation else None,
        "selection_origin": ("human" if confirmation.get("human_confirmed") else "automatic") if confirmation else None,
        "enabled_parts": confirmation.get("enabled_parts", ["face", "body"]) if confirmation else [],
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
    confirmation = current_selection(asset, settings, evidence)

    if confirmation is None:
        return _policy_payload(None, [], [{
            "subject_id": None, "part": "selection",
            "state": "uncertain", "reason": "primary_selection_unconfirmed",
        }], raw_subjects)
    stamp = "measurement_confirmation_digest" if confirmation.get("human_confirmed") else "measurement_selection_digest"
    if evidence.get(stamp) != confirmation["digest"]:
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
        parts[0]["original_reasons"] = deepcopy(face.get("reasons", [])) if face else []
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
        if "body" not in confirmation.get("enabled_parts", ["face", "body"]):
            # Disabled is not clear/not-visible. Keep it outside the judged parts.
            body_ids = []
        elif len(attached) == 1 and attached[0][1].get("association") == "pose_head_overlap":
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
    rechecked = current_selection(asset, settings, evidence)
    if rechecked is None or rechecked["digest"] != confirmation["digest"]:
        return _policy_payload(None, [], [{
            "subject_id": None, "part": "measurement",
            "state": "uncertain", "reason": "inputs_changed_during_evaluation",
        }], raw_subjects)
    return _policy_payload(confirmation, output, issues, raw_subjects)
