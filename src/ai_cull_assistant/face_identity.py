"""Optional offline SFace assistance. Features live only within one worker run.

Scores and experimental gates are review aids, never identity probabilities.
No network access, automatic enrollment, or appearance fallback occurs here.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading

import cv2
import numpy as np

from . import yunet

MODEL_PATH = Path(__file__).with_name('data') / 'face_recognition_sface_2021dec.onnx'
MODEL_SHA256 = '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'
YUNET_SHA256 = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
PREPROCESS = 'sface-v1:RGB-BGR;960;YuNet0.8;noRotation;fivePoints;L2'
MAX_FACES = 8
MAX_REFERENCES = 6
REVIEW_MARGIN = .05  # Uncalibrated conservative review guard, not fitted to test data.
REFERENCE_CONSISTENCY = .52


class IdentityUnavailable(ValueError):
    pass


def check_stop(stop):
    if stop is not None and stop.is_set():
        raise InterruptedError('人脸特征查找已停止')


def threshold_for(count):
    if not 1 <= count <= MAX_REFERENCES:
        raise ValueError('参考数量应为1至6')
    # Only 1 and 3 references were independently evaluated. Other counts are
    # deliberately conservative product guards, not claimed calibrated values.
    return .52 if count == 1 else .53 if count <= 3 else .56


@dataclass(frozen=True)
class FaceFeature:
    box: tuple[float, float, float, float]
    vector: np.ndarray
    detector_score: float


@dataclass(frozen=True)
class IdentityCandidate:
    box: tuple[float, float, float, float]
    score: float
    matched_reference: str
    reasons: tuple[str, ...] = ()


class IdentityEngine:
    def __init__(self, model_path=None):
        path = Path(model_path) if model_path is not None else MODEL_PATH
        try:
            weights = path.read_bytes()
            if hashlib.sha256(weights).hexdigest() != MODEL_SHA256:
                raise IdentityUnavailable('人脸特征模型校验失败，请修复程序安装；不会改用外观模式')
            detector_digest = hashlib.sha256(yunet.MODEL_PATH.read_bytes()).hexdigest()
            if detector_digest != YUNET_SHA256:
                raise IdentityUnavailable('人脸检测模型校验失败，请修复程序安装')
            self.recognizer = cv2.FaceRecognizerSF.create(
                'onnx', np.frombuffer(weights, np.uint8), np.empty(0, np.uint8),
                cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
        except IdentityUnavailable:
            raise
        except (OSError, cv2.error, AttributeError) as exc:
            raise IdentityUnavailable('离线人脸特征模型不可用，请修复程序安装；不会联网下载或改用外观模式') from exc
        self.fingerprint = MODEL_SHA256 + detector_digest + PREPROCESS
        self.cache = OrderedDict()

    def clear(self):
        self.cache.clear()

    def _feature(self, bgr, face):
        row = np.asarray([*face.box, *[v for p in face.landmarks for v in p], face.score], np.float32)
        if len(row) != 15 or not np.isfinite(row).all():
            raise IdentityUnavailable('缺少可靠的五点关键点')
        aligned = self.recognizer.alignCrop(bgr, row)
        vector = self.recognizer.feature(aligned).copy().reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(vector).all() or norm < 1e-8:
            raise IdentityUnavailable('人脸特征提取失败')
        vector /= norm
        vector.setflags(write=False)
        return vector

    def faces(self, rgb, stop=None):
        check_stop(stop)
        if not isinstance(rgb, np.ndarray) or rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.size == 0:
            raise IdentityUnavailable('图像不可用')
        key = hashlib.sha256(rgb.tobytes() + str(rgb.shape).encode() + self.fingerprint.encode()).hexdigest()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        h, w = rgb.shape[:2]
        scale = min(1., 960 / max(h, w))
        working = cv2.resize(rgb, (max(1, round(w*scale)), max(1, round(h*scale)))) if scale < 1 else rgb
        bgr = cv2.cvtColor(working, cv2.COLOR_RGB2BGR)
        detected = yunet.detect(bgr, .8, rotate_rescue=False)
        check_stop(stop)
        if len(detected) > MAX_FACES:
            raise IdentityUnavailable('人脸过多，请缩小范围后人工核对')
        features = []
        height, width = working.shape[:2]
        for face in detected:
            check_stop(stop)
            x, y, fw, fh = face.box
            left, top = max(0., x/width), max(0., y/height)
            right, bottom = min(1., (x+fw)/width), min(1., (y+fh)/height)
            if right <= left or bottom <= top:
                continue
            features.append(FaceFeature((left, top, right-left, bottom-top), self._feature(bgr, face), float(face.score)))
        check_stop(stop)
        result = tuple(features)
        self.cache[key] = result
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return result

    def reference(self, rgb, box, stop=None):
        from .group_face_assist import normalized_box_ok
        if not normalized_box_ok(box):
            raise IdentityUnavailable('参考框无效')
        h, w = rgb.shape[:2]
        x, y, bw, bh = box
        left, top = max(0, int((x-.3*bw)*w)), max(0, int((y-.3*bh)*h))
        right, bottom = min(w, int((x+1.3*bw)*w)), min(h, int((y+1.3*bh)*h))
        region = rgb[top:bottom, left:right]
        selected = []
        for face in self.faces(region, stop):
            fx, fy, fw, fh = face.box
            cx = (left+(fx+fw/2)*(right-left))/w
            cy = (top+(fy+fh/2)*(bottom-top))/h
            if x <= cx <= x+bw and y <= cy <= y+bh:
                selected.append(face)
        if len(selected) != 1:
            raise IdentityUnavailable('参考框内未找到可靠人脸' if not selected else '参考框内有多张人脸，请重新框选一个人')
        return selected[0].vector


def rank_features(faces, references, negatives=(), global_reasons=()):
    """Pure ranking; each reference is (source label, normalized vector)."""
    if not references:
        return []
    threshold = threshold_for(len(references))
    common = list(global_reasons)
    if any(float(np.dot(a[1], b[1])) < REFERENCE_CONSISTENCY
           for i, a in enumerate(references) for b in references[i+1:]):
        common.append('多张正参考特征不一致，请检查参考来源或移除不合适参考')
    ranked = []
    for face in faces:
        scores = [float(np.dot(face.vector, ref[1])) for ref in references]
        best = int(np.argmax(scores));score = scores[best]
        reasons = list(common)
        if score < threshold:
            reasons.append('人脸相似度低于实验门槛，目标可能缺席或证据不足')
        if negatives:
            negative = max(float(np.dot(face.vector, ref[1])) for ref in negatives)
            if negative >= threshold or negative >= score-REVIEW_MARGIN:
                reasons.append('与已排除的人脸也相似，请人工核对')
        ranked.append(IdentityCandidate(face.box, score, references[best][0], tuple(reasons)))
    ranked.sort(key=lambda candidate: candidate.score, reverse=True)
    if len(ranked) > 1 and ranked[0].score-ranked[1].score < REVIEW_MARGIN:
        from dataclasses import replace
        ranked[0] = replace(ranked[0], reasons=ranked[0].reasons+('多个人脸分数接近，请手动选择',))
    return ranked


def propose_identity_faces(reference, reference_box, targets, stop_event, on_progress=None,
                           detection_confidence=.8, additional_references=(), negative_references=()):
    """One engine/cache per run; no feature vectors are persisted or returned to UI."""
    from .group_face_assist import FaceProposal, load_rgb
    proposals = [];engine = None;problem = None
    positives = [];negatives = [];warnings = []
    try:
        check_stop(stop_event)
        engine = IdentityEngine()
        items = [(reference, reference_box), *list(additional_references or ())[:MAX_REFERENCES-1]]
        for image, box in items:
            check_stop(stop_event)
            # Do not silently drop a requested invalid positive reference.
            positives.append((image.stem, engine.reference(load_rgb(image.preview_path), box, stop_event)))
        for image, box in list(negative_references or ())[:MAX_REFERENCES]:
            check_stop(stop_event)
            try:
                negatives.append((image.stem, engine.reference(load_rgb(image.preview_path), box, stop_event)))
            except InterruptedError:
                raise
            except Exception:
                warnings.append('已排除参考无法提取，需人工核对')
    except InterruptedError:
        if engine:engine.clear()
        return []
    except Exception as exc:
        problem = str(exc) if isinstance(exc, IdentityUnavailable) else '参考或模型不可用，请重新选择参考或修复安装'
    targets = list(targets)
    try:
        for done, target in enumerate(targets, 1):
            check_stop(stop_event)
            candidates = []
            reason = problem
            if not reason:
                try:
                    candidates = rank_features(engine.faces(load_rgb(target.preview_path), stop_event), positives, negatives, warnings)
                    if not candidates:reason = '未检测到可靠人脸，目标可能缺席或漏检，请手动画框'
                except InterruptedError:
                    raise
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, IdentityUnavailable) else '本张人脸特征检查失败，请人工处理'
            if candidates:
                best = candidates[0]
                uncertain = bool(best.reasons)
                count = len(positives);threshold = threshold_for(count)
                reason = ('；'.join(best.reasons) if uncertain else '仍需确认是同一人物，目标可能不在本图')
                reason += f'；匹配参考：{best.matched_reference}；{count}张正参考，实验门槛{threshold:.2f}。余弦相似度不是身份概率'
                if count not in (1, 3):reason += '；该参考数量尚未独立校准'
                proposal = FaceProposal(target.key, reference.key, None if uncertain else best.box,
                    'uncertain' if uncertain else 'review', best.score, None, reason,
                    tuple(candidate.box for candidate in candidates))
            else:
                proposal = FaceProposal(target.key, reference.key, None, 'uncertain', None, None,
                    '人脸特征：'+(reason or '无法判断')+'；不会改用外观模式，请人工核对')
            check_stop(stop_event)
            proposals.append(proposal)
            if on_progress:on_progress(done, len(targets), proposal)
    except InterruptedError:
        pass
    finally:
        if engine:engine.clear()
    return proposals
