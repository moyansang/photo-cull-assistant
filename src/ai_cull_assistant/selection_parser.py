from __future__ import annotations

import re
from collections import OrderedDict

from .models import SelectionRecord

DEFAULT_RATING_BY_LABEL = {
    "S": 5,
    "A": 4,
    "B": 3,
    "C": 1,
    "REJECT": 1,
}

FILENAME_PATTERN = re.compile(r"\b([A-Za-z0-9_-]+)\b")
CSV_PATTERN = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*[,，]\s*([1-5])\s*$")
LABEL_LINE_PATTERN = re.compile(r"^\s*([SABC]|REJECT)\s*[:：]\s*(.+)$", re.IGNORECASE)


def parse_selection_text(text: str) -> list[SelectionRecord]:
    records: "OrderedDict[str, SelectionRecord]" = OrderedDict()
    current_label: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        csv_match = CSV_PATTERN.match(line)
        if csv_match:
            stem, rating = csv_match.groups()
            records[stem] = SelectionRecord(stem=stem, rating=int(rating), source_line=raw_line)
            current_label = None
            continue

        label_match = LABEL_LINE_PATTERN.match(line)
        if label_match:
            current_label = label_match.group(1).upper()
            trailing = label_match.group(2).strip()
            if trailing:
                for stem in _extract_stems(trailing):
                    rating = DEFAULT_RATING_BY_LABEL[current_label]
                    records[stem] = SelectionRecord(stem=stem, rating=rating, source_line=raw_line)
            continue

        if current_label is not None:
            for stem in _extract_stems(line):
                rating = DEFAULT_RATING_BY_LABEL[current_label]
                records[stem] = SelectionRecord(stem=stem, rating=rating, source_line=raw_line)
            continue

        for stem in _extract_stems(line):
            records[stem] = SelectionRecord(stem=stem, rating=5, source_line=raw_line)

    return list(records.values())


def _extract_stems(text: str) -> list[str]:
    parts = re.split(r"[\s,，;；]+", text.strip())
    stems: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        match = FILENAME_PATTERN.search(part)
        if match:
            stems.append(match.group(1))
    return stems
