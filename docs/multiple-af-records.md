# Multiple source-bound AF records

The Canon EOS R3 AFInfo2 display adapter now carries every valid active record
from the union of selected and camera-reported-in-focus bitmasks. These states
remain separate tri-state values: an absent mask is unknown, not false. Inactive
positive-area grid entries are retained in metadata but are not displayed as
focus records. No first, nearest, largest or representative point is selected.

Multi-record results leave singleton coordinates and index unset. A finite list
of normalized centers/areas is transformed once for EXIF orientation and sealed
with its actual source, parser, preview pixels and evidence. Any altered role,
dropped record, stale source, changed pixel or malformed field invalidates the
collection. The same JPEG and strictly bound Canon native RAW preview routes
support it. Other camera adapters retain their current singleton semantics.

The grouping-preview display uses the existing accent color and distinct line
patterns: long dashed = selected, dotted = camera-reported in-focus, dash-dot =
both. The status line supplies this legend and explicitly disclaims optical
sharpness. Every record has its own center cross and recorded area; no rectangle
is invented by this change. The canvas prevalidates the whole collection before
writing any marker. It never displays a valid prefix of a corrupt collection.

The display-only resource ceiling is 4096 active records. Larger parser records
remain available as metadata; the whole overlay is hidden, never truncated.
This is a renderer safety limit, not a claim about camera tag semantics.

Existing real R3 fixtures each contain one active record. Multiple-point evidence
in this checkpoint uses synthetic arrays, masks and JPEG pixels. Eight directions,
signed16 mask words, indices beyond16, disjoint roles and tamper tests validate
implementation contracts; they are not additional camera calibration or a
real-world multi-point accuracy measurement. GUI/Windows runs remain unverified.

No quality thresholds, grouping, manual decisions or automatic person selection
are changed. Camera-reported in-focus is not a claim that the eye/person is sharp.
