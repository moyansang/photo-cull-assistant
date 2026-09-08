from __future__ import annotations

from pathlib import Path
import re

XMP_NS_URI = "http://ns.adobe.com/xap/1.0/"
XMPDM_NS_URI = "http://ns.adobe.com/xmp/1.0/DynamicMedia/"

RATING_TEMPLATE = """<?xpacket begin=\"﻿\" id=\"W5M0MpCehiHzreSzNTczkc9d\"?>
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">
  <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">
    <rdf:Description rdf:about=\"\" xmlns:xmp=\"http://ns.adobe.com/xap/1.0/\" xmp:Rating=\"{rating}\" />
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end=\"w\"?>
"""

REJECT_TEMPLATE = """<?xpacket begin=\"﻿\" id=\"W5M0MpCehiHzreSzNTczkc9d\"?>
<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">
  <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">
    <rdf:Description rdf:about=\"\" xmlns:xmpDM=\"http://ns.adobe.com/xmp/1.0/DynamicMedia/\" xmpDM:pick=\"-1\" xmpDM:good=\"false\" />
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end=\"w\"?>
"""


def sidecar_path(image_path: Path) -> Path:
    return image_path.with_suffix(".xmp")


def write_rating(image_path: Path, rating: int) -> Path:
    """Write Lightroom/Adobe star rating while preserving other XMP fields."""
    xmp_path = sidecar_path(image_path)
    if not xmp_path.exists():
        xmp_path.write_text(RATING_TEMPLATE.format(rating=rating), encoding="utf-8")
        return xmp_path

    content = xmp_path.read_text(encoding="utf-8", errors="ignore")
    if "xmp:Rating" in content:
        content = re.sub(r'xmp:Rating="-?\d+(?:\.\d+)?"', f'xmp:Rating="{rating}"', content)
    elif "<rdf:Description" in content:
        attrs = []
        if f'xmlns:xmp="{XMP_NS_URI}"' not in content:
            attrs.append(f'xmlns:xmp="{XMP_NS_URI}"')
        attrs.append(f'xmp:Rating="{rating}"')
        content = _append_description_attributes(content, attrs)
    else:
        content = RATING_TEMPLATE.format(rating=rating)
    xmp_path.write_text(content, encoding="utf-8")
    return xmp_path


def write_reject_flag(image_path: Path) -> Path:
    """Write Lightroom Classic Pick/Reject flag without changing star rating.

    Lightroom Classic 13.2+ stores flag state in xmpDM:pick and xmpDM:good.
    Rejected = pick -1 + good false.
    """
    xmp_path = sidecar_path(image_path)
    if not xmp_path.exists():
        xmp_path.write_text(REJECT_TEMPLATE, encoding="utf-8")
        return xmp_path

    content = xmp_path.read_text(encoding="utf-8", errors="ignore")
    if "<rdf:Description" not in content:
        content = REJECT_TEMPLATE
    else:
        attrs: list[str] = []
        if f'xmlns:xmpDM="{XMPDM_NS_URI}"' not in content:
            attrs.append(f'xmlns:xmpDM="{XMPDM_NS_URI}"')

        if re.search(r'xmpDM:pick="[^\"]*"', content):
            content = re.sub(r'xmpDM:pick="[^\"]*"', 'xmpDM:pick="-1"', content)
        else:
            attrs.append('xmpDM:pick="-1"')

        if re.search(r'xmpDM:good="[^\"]*"', content):
            content = re.sub(r'xmpDM:good="[^\"]*"', 'xmpDM:good="false"', content)
        else:
            attrs.append('xmpDM:good="false"')

        if attrs:
            content = _append_description_attributes(content, attrs)

    xmp_path.write_text(content, encoding="utf-8")
    return xmp_path


def _append_description_attributes(content: str, attrs: list[str]) -> str:
    if not attrs:
        return content
    attr_text = " " + " ".join(attrs)
    return re.sub(
        r"(<rdf:Description\b[^>]*?)(\s*/?>)",
        lambda match: f"{match.group(1)}{attr_text}{match.group(2)}",
        content,
        count=1,
    )
