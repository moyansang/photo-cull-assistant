from dataclasses import dataclass, field, replace
import math


@dataclass(frozen=True)
class CropSettings:
    scale_factor: float = 1.0
    shift_factor: float = 0.0
    aspect_ratio: str = "124:150"
    detection_confidence: float = .8

    photos: dict = field(default_factory=dict)

    def key(self, asset):
        return str(asset.primary_path.resolve()).casefold()

    def for_asset(self, asset):
        values = self.photos.get(self.key(asset), {})
        return replace(CropSettings.from_dict(values), detection_confidence=self.detection_confidence)

    @classmethod
    def from_dict(cls, values):
        try:
            scale = float(values.get("scale_factor", 1))
            shift = float(values.get("shift_factor", 0))
            confidence = float(values.get("detection_confidence", .8))
            if not math.isfinite(confidence):
                confidence = .8
            ratio = values.get("aspect_ratio", "124:150")
            if not math.isfinite(scale) or not math.isfinite(shift):
                return cls()
            return cls(max(.6, min(2.0, scale)), max(-.5, min(.5, shift)), ratio if ratio in ("124:150", "1:1", "3:4") else "124:150", max(.7, min(.95, confidence)), values.get("photos", {}) if isinstance(values.get("photos", {}), dict) else {})
        except (AttributeError, ValueError, TypeError):
            return cls()


def crop_bounds(image_size, head, settings):
    width, height = image_size
    x, y, w, h = head
    cx, cy = (x + w / 2) * width, (y + h / 2) * height
    base_h = h * height
    cy += settings.shift_factor * base_h
    a, b = map(float, settings.aspect_ratio.split(":"))
    crop_h = base_h * settings.scale_factor
    crop_w = crop_h * a / b
    fit = min(1, width / crop_w, height / crop_h)
    crop_w, crop_h = crop_w * fit, crop_h * fit
    left = max(0, min(cx - crop_w / 2, width - crop_w))
    top = max(0, min(cy - crop_h / 2, height - crop_h))
    return round(left), round(top), round(left + crop_w), round(top + crop_h)
