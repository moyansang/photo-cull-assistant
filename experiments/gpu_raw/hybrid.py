"""Serial experimental policy. No production imports this module.

20% is a provisional guard band, not a calibrated probability of correctness.
The gates mirror native-face-v4-kps; fail closed if that version changes.
"""
from dataclasses import replace
from math import isfinite
from time import perf_counter
from unittest.mock import patch

from ai_cull_assistant.face_focus import assess_asset_focus, load_full_image, VERSION
from ai_cull_assistant.shared_decode import shared_decode

POLICY_VERSION = 'opencl-mhc-cpu-guard-v1'
FOCUS_VERSION = 'native-face-v4-kps'
MARGIN = .20


def review_reasons(result):
    if VERSION != FOCUS_VERSION:
        return ['focus_version_changed']
    e = result.focus_evidence or {}
    # Missing faces cannot be fixed by changing the RAW developer. Retain
    # their non-clear result; never fabricate a successful clarity assessment.
    if not result.face_found:
        return []
    if e.get('state') != 'clear':
        return ['non_clear_gpu_result']
    try:
        eye, core = e['regions']['eye_band'], e['regions']['face_core']
        lower = {
            'eye_laplacian': (eye['fine']['laplacian_normalized'], .0045),
            'core_laplacian': (core['fine']['laplacian_normalized'], .0060),
            'eye_curvature': (eye['fine']['edge_curvature'], .18),
            'native_curvature': (eye['native_fine']['edge_curvature'], .215),
            'eye_gradient': (eye['fine']['gradient_p90_normalized'], .25),
            'coarse_laplacian': (eye['coarse']['laplacian_normalized'], .0008),
            'eye_contrast': (eye['fine']['contrast'], .10),
            'core_contrast': (core['fine']['contrast'], .08),
            'eye_pixels': (e['native_eye_distance'], 56.),
        }
        upper = {
            'eye_noise': (eye['fine_to_coarse_ratio'], 12.),
            'core_noise': (core['fine_to_coarse_ratio'], 14.),
            # Conservative: review strong directionality without requiring
            # the other two conjuncts of the production smear rule.
            'directionality': (eye['fine']['orientation_coherence'], .72),
        }
        if not e['landmarks_used'] or not all(
            isfinite(v) for v, _ in [*lower.values(), *upper.values()]
        ):
            return ['invalid_evidence']
        return ([k for k, (v, t) in lower.items() if v <= t * (1 + MARGIN)]
                + [k for k, (v, t) in upper.items() if v >= t * (1 - MARGIN)])
    except (KeyError, TypeError, ValueError):
        return ['incomplete_evidence']


def choose_result(gpu, reasons, cpu_check):
    """CPU assessment is authoritative when requested; failures stay uncertain."""
    audit = dict(policy=POLICY_VERSION, triggers=reasons, gpu_reason=gpu.reason,
                 gpu_evidence=gpu.focus_evidence, cpu_reviewed=bool(reasons))
    selected = gpu
    if reasons:
        start = perf_counter()
        try:
            selected = cpu_check()
            audit['cpu_reason'] = selected.reason
            if (selected.focus_evidence or {}).get('state') not in {'clear', 'uncertain', 'severe_blur'}:
                raise ValueError('CPU returned no valid clarity evidence')
        except Exception as exc:
            audit['cpu_error'] = type(exc).__name__ + ': ' + str(exc)
            selected = replace(gpu, rejected=False, reason='face_focus_uncertain',
                focus_evidence={'state': 'uncertain', 'reasons': ['cpu_review_failed']})
        audit['cpu_review_seconds'] = perf_counter() - start
    return replace(selected, analysis_version=POLICY_VERSION,
                   focus_evidence={**(selected.focus_evidence or {}), 'hybrid': audit})


def assess_hybrid(asset, *, crop_settings=None, cache_dir=None):
    # Deliberately bypass native-focus cache: its key lacks RAW backend ID.
    # A nested scope also prevents the CPU review reusing the GPU image from
    # the enclosing per-photo shared_decode scope. Patches are serial-only.
    gpu = assess_asset_focus(asset, crop_settings=crop_settings, cache_dir=None)
    reasons = review_reasons(gpu)
    def cpu_check():
        with shared_decode(asset), patch('ai_cull_assistant.face_focus.load_full_image', load_full_image):
            return assess_asset_focus(asset, crop_settings=crop_settings, cache_dir=None)
    return choose_result(gpu, reasons, cpu_check)
