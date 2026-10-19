# Nikon Z5II AF metadata and source-bound diagnostic display

This adapter is limited to `NIKON CORPORATION`, model `NIKON Z5_2`,
AFInfo2 version `0402`. It is a read-only display adapter, not an AF-based
subject selector or a sharpness classifier. Other Nikon models/versions remain
unsupported. Existing culling, grouping and manual decisions are unchanged.

## Recorded semantics

The official ExifTool AFInfo2V0400 definitions give AFImageWidth/Height,
AFAreaX/YPosition, AFAreaWidth/Height in the AF output image frame. The recorded
center uses the top-left origin. The sample's frame is 6048×4032; its NEF storage
raster is 6064×4040. CropArea `8 4 6048 4032` locates the output in storage.
It does not require adding (8,4) to AF coordinates already in the output frame.
EXIF orientations 1–8 transform the record once, after binding source geometry.

FocusResult 0/1 is preserved as the last AF operation result for AF-S/AF-C.
It is never a measured optical sharpness result. AF-A is recognized as a photo
mode but does not inherit those result semantics. Missing AF coordinates remain
unknown. Missing both area dimensions permits a point-only representation;
partial, malformed or out-of-frame area dimensions do not invent a rectangle.
The adapter does not decode AFPointsUsed as a guessed coordinate grid.

## Preview provenance

The RAW path reads the same NEF's SubIFD:JpgFromRaw into a private snapshot,
checks the source/parser/pixels, and applies main orientation only for this
bounded extraction profile. It does not decode the RAW sensor image or look for
a sibling JPEG. Eligibility additionally requires explicit native FX crop,
matching sensor bounds and output dimensions, zero retouch and distortion Off.
Extra recorded crop/warp or contradictory dimensions suppress the overlay.
A usable embedded picture remains viewable when AF mapping is unknown.

JPEG display separately requires its own full-frame evidence, with explicit
zero retouch and distortion Off. The public camera-reprocessed JPEG has
RetouchHistory 19: its AF fields parse, but the UI does not draw them merely
because dimensions match. No RAW point is transferred to that JPEG.

## Evidence and limits

Actual public NEF: `nikon_9551248880.nef`, AF center (3048,1671), area 204×204,
6048×4032 output, EXIF orientation 1. Same-file extraction and overlay execute
successfully without modifying the source. The accompanying reprocessed JPEG
retains the numeric record but is conservatively hidden. An independent
third-party screenshot has about 1.51 px maximum edge discrepancy under this
coordinate convention. It supports numeric display agreement, not camera-LCD
calibration, actual focused-person identity or sharpness. Other directions,
mode variants and invalid cases are synthetic contract tests, not additional
real-camera validation. No real GUI run is claimed in the headless environment.

Sources:
- https://github.com/exiftool/exiftool/blob/2200871d9cef988051d2a99d67df3bda6cbb30a8/lib/Image/ExifTool/Nikon.pm
- https://onlinemanual.nikonimglib.com/z5II/en/focus_37.html

No research photos, third-party plugin source or ExifTool executable are bundled.
