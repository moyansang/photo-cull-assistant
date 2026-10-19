"""Strict, source-bound AI technical review protocol.

No model calls, settings writes, rating changes, or manual-decision mutation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from typing import Any, Mapping


PROTOCOL_VERSION = "focus-technical-v4"
PROMPT_VERSION = PROTOCOL_VERSION

_RESPONSE_KEYS = {
    "version", "photo_identity", "scope_digest",
    "confirmation_digest", "subject_id", "context_digest",
    "reason", "parts",
}
_PART_KEYS = {
    "part", "visibility", "severity", "evidence_sufficient",
    "location", "reason",
}
_CONTEXT_KEYS = {
    "version", "photo_identity", "scope_digest",
    "confirmation_digest", "subject_id", "mode",
    "required_parts", "evidence_parts", "not_visible_parts", "digest",
}
_SCOPE_KEYS = {
    "version", "mode", "photo_identity", "source_digest",
    "settings_digest", "confirmation_digest", "subjects", "digest",
}
_SELECTION_SCOPE_KEYS = {"selection_origin", "selection_digest", "enabled_parts"}
_ITEM_KEYS = _RESPONSE_KEYS | {"context"}
_ENVELOPE_KEYS = {
    "version", "prompt_version", "scope",
    "photo_identity", "confirmation_digest",
    "status", "severity", "evidence_sufficient",
    "recommendation", "reason", "participants", "issues",
}
_OPTIONAL_ENVELOPE_KEYS = {
    "source", "raw_response", "usage", "audit",
    "technical_fingerprint", "local_conflict", "local_conflict_reason",
}
_SEVERITIES = {"clear", "mild", "severe", "uncertain"}
_VISIBILITIES = {"visible", "not_visible", "unknown"}


class FocusProtocolError(ValueError):
    pass


def _canonical(value):
    try:
        return json.loads(json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        ))
    except (TypeError, ValueError, OverflowError) as exc:
        raise FocusProtocolError("技术复核数据不是有效 JSON。") from exc


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _text(value, *, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise FocusProtocolError("技术复核文本字段无效。")
    return value.strip()


def _hex(value):
    return (
        isinstance(value, str) and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _unique_object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _reject_constant(_value):
    raise ValueError("non-finite JSON constant")


def _load_json(text):
    if not isinstance(text, str) or not text.strip():
        raise FocusProtocolError("AI 技术复核未返回 JSON 文本。")
    try:
        return json.loads(
            text, object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError) as exc:
        raise FocusProtocolError("AI 技术复核返回的不是严格 JSON。") from exc


def policy_matches_scope(policy, evidence, scope):
    """Accept body associations only from this selection's current measurement."""
    if not isinstance(policy, dict) or not isinstance(evidence, dict):
        return False
    from .primary_quality import POLICY_VERSION
    if policy.get("version") != POLICY_VERSION:
        return False
    if "selection_origin" in scope:
        digest = scope["selection_digest"]
        return (
            policy.get("selection_origin") == scope["selection_origin"]
            and policy.get("selection_digest") == digest
            and policy.get("confirmation_digest") == scope["confirmation_digest"]
            and policy.get("enabled_parts") == scope["enabled_parts"]
            and evidence.get("measurement_confirmation_digest" if scope["selection_origin"] == "human"
                             else "measurement_selection_digest") == digest
        )
    digest = scope.get("confirmation_digest")
    return (_hex(digest) and policy.get("confirmation_digest") == digest
            and evidence.get("measurement_confirmation_digest") == digest)


def make_scope(asset, settings):
    """Capture current photo identity and the complete allowed subject set."""
    from .image_decode import asset_source_signature

    mode = getattr(asset, "analysis_mode", "portrait")
    if mode not in {"portrait", "landscape"}:
        raise FocusProtocolError("不支持的分析模式。")
    source = _canonical(asset_source_signature(asset))
    if not source or any(
        not isinstance(item, list) or len(item) != 2
        or not isinstance(item[1], list) or len(item[1]) < 2
        or item[1][1] is None
        for item in source
    ):
        raise FocusProtocolError("无法确认当前照片来源。")

    selection_fields = {}
    if mode == "portrait":
        from .primary_quality import current_selection, manual_body_measurement_current

        confirmation = current_selection(asset, settings)
        if confirmation is None:
            raise FocusProtocolError("当前主要主体范围不可靠，请先确认主要主体范围，再进行技术复核。")
        # Both reads must describe the same source version.
        if _canonical(confirmation["source_signature"]) != source:
            raise FocusProtocolError("照片来源在校验期间发生变化。")
        human = confirmation.get("human_confirmed") is True
        confirmation_digest = confirmation["digest"] if human else None
        if not human or "enabled_parts" in confirmation:
            selection_fields = {
                "selection_origin": "human" if human else "automatic",
                "selection_digest": confirmation["digest"],
                "enabled_parts": confirmation.get("enabled_parts", ["face", "body"]),
            }
        subjects = [
            {"id": region["id"], "kind": region["kind"]}
            for region in confirmation["regions"]
        ]
        # Only the current local policy's unique pose/head association can
        # identify two manually selected ROIs as the same subject.
        evidence = getattr(asset, 'clarity_evidence', None) or {}
        evidence = evidence if isinstance(evidence, dict) else {}
        policy = evidence.get('primary_quality') or {}
        policy = policy if isinstance(policy, dict) else {}
        if manual_body_measurement_current(asset, settings, evidence) and policy_matches_scope(policy, evidence, dict(
                confirmation_digest=confirmation_digest, **selection_fields)):
            people = policy.get('subjects')
            people = people if isinstance(people,list) else []
            merged = {rid for person in people
                      if isinstance(person,dict) and str(person.get('id', '')).startswith('face:')
                      for rid in person.get('region_ids', [])
                      if str(rid).startswith('body:')}
            subjects = [item for item in subjects if item['id'] not in merged]
        settings_digest = confirmation["parameters_digest"]
    else:
        # Landscape does not require a portrait confirmation or face ROI.
        confirmation_digest = None
        subjects = [{"id": "scene:frame", "kind": "scene"}]
        values = asdict(settings.for_asset(asset))
        values.pop("photos", None)
        values["detection_confidence"] = settings.detection_confidence
        settings_digest = _digest(_canonical(values))

    source_digest = _digest(source)
    scope = {
        "version": PROTOCOL_VERSION,
        "mode": mode,
        # Opaque identity: no path or filename needs to go into the prompt.
        "photo_identity": "photo:" + source_digest,
        "source_digest": source_digest,
        "settings_digest": settings_digest,
        "confirmation_digest": confirmation_digest,
        "subjects": sorted(subjects, key=lambda item: item["id"]),
        **selection_fields,
    }
    scope["digest"] = _digest(scope)
    return _validate_scope(scope)


def _validate_scope(scope):
    value = _canonical(scope)
    if not isinstance(value, dict) or set(value) not in (_SCOPE_KEYS, _SCOPE_KEYS | _SELECTION_SCOPE_KEYS):
        raise FocusProtocolError("技术复核范围字段无效。")
    if value["version"] != PROTOCOL_VERSION:
        raise FocusProtocolError("技术复核协议版本不匹配。")
    if value["mode"] not in {"portrait", "landscape"}:
        raise FocusProtocolError("技术复核模式无效。")
    if not _hex(value["source_digest"]) or not _hex(value["settings_digest"]):
        raise FocusProtocolError("技术复核来源摘要无效。")
    if value["photo_identity"] != "photo:" + value["source_digest"]:
        raise FocusProtocolError("照片标识与来源摘要不匹配。")
    explicit = _SELECTION_SCOPE_KEYS <= set(value)
    if explicit:
        if (value["mode"] != "portrait"
                or value["selection_origin"] not in {"human", "automatic"}
                or not _hex(value["selection_digest"])
                or value["enabled_parts"] not in (["face"], ["face", "body"])):
            raise FocusProtocolError("技术复核主体来源或启用部位无效。")
        if value["selection_origin"] == "automatic":
            if value["confirmation_digest"] is not None:
                raise FocusProtocolError("自动主体不能携带人工确认摘要。")
        elif value["selection_digest"] != value["confirmation_digest"]:
            raise FocusProtocolError("人工选择摘要与确认摘要不匹配。")
    if value["mode"] == "portrait":
        if not (explicit and value["selection_origin"] == "automatic") and not _hex(value["confirmation_digest"]):
            raise FocusProtocolError("缺少主要主体确认摘要。")
    elif value["confirmation_digest"] is not None:
        raise FocusProtocolError("景物复核不能携带人像确认摘要。")
    subjects = value["subjects"]
    if not isinstance(subjects, list) or not subjects:
        raise FocusProtocolError("技术复核主体列表为空。")
    identities = []
    for subject in subjects:
        if not isinstance(subject, dict) or set(subject) != {"id", "kind"}:
            raise FocusProtocolError("技术复核主体字段无效。")
        kind, identity = subject["kind"], subject["id"]
        if value["mode"] == "landscape":
            if subject != {"id": "scene:frame", "kind": "scene"}:
                raise FocusProtocolError("景物主体标识无效。")
        elif (
            kind not in {"face", "body"} or not isinstance(identity, str)
            or not identity.startswith(kind + ":")
            or len(identity) <= len(kind) + 1
        ):
            raise FocusProtocolError("人像主体标识无效。")
        identities.append(identity)
    if len(set(identities)) != len(identities):
        raise FocusProtocolError("技术复核主体标识重复。")
    if value["mode"] == "landscape" and len(subjects) != 1:
        raise FocusProtocolError("景物复核范围无效。")
    payload = {key: item for key, item in value.items() if key != "digest"}
    if value["digest"] != _digest(payload):
        raise FocusProtocolError("技术复核范围摘要不匹配。")
    return value


def make_context(scope, subject_id, *, evidence_parts=(), not_visible_parts=()):
    """Bind one request to a confirmed subject.

    evidence_parts describes actual supplied native detail images.
    not_visible_parts must come from current local visibility evidence.
    """
    scope = _validate_scope(scope)
    if subject_id not in {item["id"] for item in scope["subjects"]}:
        raise FocusProtocolError("请求主体不在当前确认范围内。")
    required = ["scene"] if scope["mode"] == "landscape" else scope.get("enabled_parts", ["face", "body"])
    available, invisible = list(evidence_parts), list(not_visible_parts)
    for values in (available, invisible):
        if len(set(values)) != len(values) or not set(values) <= set(required):
            raise FocusProtocolError("技术复核部位范围无效。")
    # The current product has local evidence for invisible headshot bodies.
    # Missing face details do not establish an invisible face.
    if set(invisible) - {"body"} or set(available) & set(invisible):
        raise FocusProtocolError("不可见部位与证据范围冲突。")
    context = {
        "version": PROTOCOL_VERSION,
        "photo_identity": scope["photo_identity"],
        "scope_digest": scope["digest"],
        "confirmation_digest": scope["confirmation_digest"],
        "subject_id": subject_id,
        "mode": scope["mode"],
        "required_parts": required,
        "evidence_parts": sorted(available),
        "not_visible_parts": sorted(invisible),
    }
    context["digest"] = _digest(context)
    return context


def _validate_context(context, scope):
    if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS:
        raise FocusProtocolError("技术复核请求上下文无效。")
    for field in ("evidence_parts", "not_visible_parts"):
        if not isinstance(context.get(field), list):
            raise FocusProtocolError("技术复核部位上下文无效。")
    expected = make_context(
        scope, context.get("subject_id"),
        evidence_parts=context["evidence_parts"],
        not_visible_parts=context["not_visible_parts"],
    )
    if _canonical(context) != expected:
        raise FocusProtocolError("技术复核请求上下文不匹配。")
    return expected


def response_template(context):
    """Prompt schema example; its uncertain values are not a model verdict."""
    return {
        "version": PROTOCOL_VERSION,
        "photo_identity": context["photo_identity"],
        "scope_digest": context["scope_digest"],
        "confirmation_digest": context["confirmation_digest"],
        "subject_id": context["subject_id"],
        "context_digest": context["digest"],
        "reason": "逐部位说明实际观察到的细节。",
        "parts": [
            {
                "part": name,
                "visibility": "unknown",
                "severity": "uncertain",
                "evidence_sufficient": False,
                "location": name,
                "reason": "当前部位尚未获得充分证据。",
            }
            for name in context["required_parts"]
        ],
    }


def prompt_contract(context):
    """Append to the image-specific technical prompt."""
    return (
        "\n只评价此请求的主体和部位，不评价其他人，不给星级。"
        "明确区分严重模糊、轻微模糊、清楚与证据不足。"
        "severity 只能是 clear、mild、severe、uncertain。"
        "visibility 只能是 visible、not_visible、unknown。"
        "evidence_sufficient 必须是 JSON 布尔值。"
        "缺少原生细节、遮挡、低纹理或无法定位时，使用 uncertain 和 false；"
        "框内多人、主体对应不明或部位归属有歧义时，同样使用 uncertain 和 false；"
        "不能把噪声、皮肤平滑或背景虚化当作严重主体模糊。"
        "脸清楚不能证明身体清楚，身体清楚不能证明脸或眼睛清楚。"
        "仅在 evidence_parts 中列出的部位允许作充分质量判断；"
        "not_visible 仅允许用于 not_visible_parts 中列出的部位。"
        "location 指出具体位置，例如眼区、躯干、左前臂；"
        "reason 描述实际边缘证据，不能仅复述结论。"
        "严重细节损失不自动证明运动成因。"
        "身体严重结论只针对躯干、手臂和腿等主要部位；指尖、发梢、衣摆的轻微运动不作为严重弃置证据。"
        "必须完整返回 required_parts，每项一次。"
        "逐字照抄所有身份和摘要字段，只输出严格 JSON，不增加字段。"
        "\n请求范围：" + json.dumps(
            {key: context[key] for key in (
                "required_parts", "evidence_parts", "not_visible_parts",
            )}, ensure_ascii=False,
        )
        + "\n返回格式：" + json.dumps(
            response_template(context), ensure_ascii=False,
        )
    )


def _validate_response(value, context):
    if not isinstance(value, dict) or set(value) != _RESPONSE_KEYS:
        raise FocusProtocolError("AI 技术复核返回字段无效。")
    expected = {
        "version": PROTOCOL_VERSION,
        "photo_identity": context["photo_identity"],
        "scope_digest": context["scope_digest"],
        "confirmation_digest": context["confirmation_digest"],
        "subject_id": context["subject_id"],
        "context_digest": context["digest"],
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise FocusProtocolError("AI 返回的照片、主体或确认摘要不匹配。")
    reason = _text(value["reason"])
    parts = value["parts"]
    if not isinstance(parts, list) or len(parts) != len(context["required_parts"]):
        raise FocusProtocolError("AI 未完整返回所需部位。")
    output, seen = [], set()
    for part in parts:
        if not isinstance(part, dict) or set(part) != _PART_KEYS:
            raise FocusProtocolError("AI 部位质量字段无效。")
        name = part["part"]
        if not isinstance(name, str) or name not in context["required_parts"] or name in seen:
            raise FocusProtocolError("AI 部位缺失、重复或超出范围。")
        seen.add(name)
        visibility, severity = part["visibility"], part["severity"]
        sufficient = part["evidence_sufficient"]
        if (
            not isinstance(visibility, str) or visibility not in _VISIBILITIES
            or not isinstance(severity, str) or severity not in _SEVERITIES
            or type(sufficient) is not bool
        ):
            raise FocusProtocolError("AI 部位质量枚举或布尔值无效。")
        if visibility == "not_visible":
            if name not in context["not_visible_parts"] or severity != "uncertain" or sufficient:
                raise FocusProtocolError("AI 不可见部位判断缺少支持。")
        elif visibility == "unknown":
            if severity != "uncertain" or sufficient:
                raise FocusProtocolError("未知部位不能提供明确质量结论。")
        elif sufficient:
            if name not in context["evidence_parts"] or severity == "uncertain":
                raise FocusProtocolError("AI 充分证据声明与输入范围冲突。")
        elif severity != "uncertain":
            raise FocusProtocolError("证据不足不能声明明确质量等级。")
        output.append({
            **part,
            "location": _text(part["location"], maximum=160),
            "reason": _text(part["reason"]),
        })
    if seen != set(context["required_parts"]):
        raise FocusProtocolError("AI 未覆盖全部所需部位。")
    output.sort(key=lambda item: context["required_parts"].index(item["part"]))
    return {**expected, "reason": reason, "parts": output}


def parse_response(text, context):
    """Strictly parse one response against its captured request context."""
    parsed = _validate_response(_load_json(text), context)
    return {**parsed, "context": deepcopy(context)}


def unavailable_result(context, reason):
    """Explicit local placeholder; never represents a successful API verdict."""
    response = response_template(context)
    response["reason"] = _text(reason)
    for part in response["parts"]:
        part["reason"] = response["reason"]
        if part["part"] in context["not_visible_parts"]:
            part["visibility"] = "not_visible"
            part["reason"] = "当前本地证据确认此部位不可见。"
    return {**_validate_response(response, context), "context": deepcopy(context)}


def _validated_item(item, scope):
    if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
        raise FocusProtocolError("技术复核主体结果字段无效。")
    context = _validate_context(item["context"], scope)
    response = {key: item[key] for key in _RESPONSE_KEYS}
    return {**_validate_response(response, context), "context": context}


def assemble_result(scope, items):
    """One severe supported part wins; otherwise complete clarity is required."""
    scope = _validate_scope(scope)
    if not isinstance(items, list):
        raise FocusProtocolError("技术复核主体结果必须是列表。")
    validated = [_validated_item(item, scope) for item in items]
    by_id = {}
    for item in validated:
        if item["subject_id"] in by_id:
            raise FocusProtocolError("技术复核主体结果重复。")
        by_id[item["subject_id"]] = item

    expected_ids = [item["id"] for item in scope["subjects"]]
    if set(by_id) != set(expected_ids):
        raise FocusProtocolError("技术复核结果未覆盖完整确认范围。")
    participants = [by_id[identity] for identity in expected_ids]
    issues, severe, all_clear = [], False, True
    for item in participants:
        for part in item["parts"]:
            supported_severe = (
                part["visibility"] == "visible"
                and part["evidence_sufficient"] is True
                and part["severity"] == "severe"
            )
            supported_clear = (
                part["visibility"] == "visible"
                and part["evidence_sufficient"] is True
                and part["severity"] == "clear"
            )
            permitted_absence = (
                part["visibility"] == "not_visible"
                and part["part"] in item["context"]["not_visible_parts"]
            )
            severe |= supported_severe
            all_clear &= supported_clear or permitted_absence
            if not supported_clear and not permitted_absence:
                issues.append({
                    "subject_id": item["subject_id"],
                    **deepcopy(part),
                })

    recommendation = "reject" if severe else "keep" if all_clear else "review"
    status = {"reject": "blur", "keep": "clear", "review": "uncertain"}[recommendation]
    severity = {"reject": "severe", "keep": "clear", "review": "uncertain"}[recommendation]
    if severe:
        issue = next(item for item in issues if (
            item["severity"] == "severe" and item["evidence_sufficient"] is True
        ))
        person = expected_ids.index(issue['subject_id']) + 1
        reason = f"人物 {person} · {issue['location']}：{issue['reason']}"
    elif all_clear:
        reason = ("当前主体的可见必需部位均有充分清楚证据。"
                  if scope.get("selection_origin") == "automatic"
                  else "已确认主体的可见必需部位均有充分清楚证据。")
    else:
        issue = issues[0]
        person = expected_ids.index(issue['subject_id']) + 1
        reason = f"人物 {person} · {issue['location']}：{issue['reason']}"
    return {
        "version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "scope": scope,
        "photo_identity": scope["photo_identity"],
        "confirmation_digest": scope["confirmation_digest"],
        "status": status,
        "severity": severity,
        # Sufficient to support the recommendation; does not mean every part
        # was assessed. Per-part flags remain authoritative.
        "evidence_sufficient": bool(severe or all_clear),
        "recommendation": recommendation,
        "reason": reason,
        "participants": participants,
        "issues": issues,
    }


def validate_result(value, *, scope=None):
    """Validate a stored aggregate; optionally require a current caller scope."""
    if not isinstance(value, Mapping):
        raise FocusProtocolError("技术复核结果不是对象。")
    keys = set(value)
    if not _ENVELOPE_KEYS <= keys or keys - _ENVELOPE_KEYS - _OPTIONAL_ENVELOPE_KEYS:
        raise FocusProtocolError("技术复核结果字段或版本无效。")
    embedded = _validate_scope(value["scope"])
    if scope is not None and embedded != _validate_scope(scope):
        raise FocusProtocolError("技术复核已不属于当前照片或确认范围。")
    rebuilt = assemble_result(embedded, value["participants"])
    if any(_canonical(value[key]) != rebuilt[key] for key in _ENVELOPE_KEYS):
        raise FocusProtocolError("技术复核汇总结论与逐部位证据不一致。")
    return deepcopy(dict(value))


def result_is_current(value, scope):
    try:
        validate_result(value, scope=scope)
        return True
    except (FocusProtocolError, KeyError, TypeError, ValueError):
        return False


def current_result(value, asset, settings):
    """Asset/settings boundary helper. Returns a validated copy or None."""
    try:
        return validate_result(value, scope=make_scope(asset, settings))
    except (
        FocusProtocolError, AttributeError, KeyError,
        OSError, TypeError, ValueError, OverflowError,
    ):
        return None
