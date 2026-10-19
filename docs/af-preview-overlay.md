# Group preview AF-area reference

The group page has a default-off AF-area toggle for the exact selected photo.
Turning it off or navigating invalidates earlier work. It does not confirm a
person, change a crop, save a decision, rescan/regroup or affect image quality.

## Bounded same-JPEG path

The supported diagnostic path reads a dedicated full-frame preview and AF
metadata from the same camera-original JPEG on a background worker. A paired
JPEG is used explicitly when present; otherwise a primary JPEG may be used.
The selected card uses the clean pixels from that same returned bundle, with
an AF-area crosshair/dashed box and a visible reference-only label. Other cards
retain their existing thumbnails. Turning the toggle off restores the normal
preview. RAW-only assets do not borrow another file or guess from a cached image.

Current display support is model/geometry scoped: Panasonic DC-S5M2 single-area
metadata, Sony ILCE-6600 playback focus location, or Canon EOS R3 AFInfo2
unambiguous recorded point. These profiles require 6000×4000
stored JPEG primary frame and valid EXIF direction. See sony-af-location.md for
Sony-specific modes, dimensions, center fallback and point-only semantics;
canon-af-records.md explains selected/in-focus roles and per-file coordinates.
The MPO-labelled camera JPEG is handled only as its zeroth/main JPEG frame;
additional MPO frames are never selected. Source dimensions must agree with the
available camera metadata. Other sizes/crops/models/modes remain unavailable for
actual UI marks until independently checked. Missing area size yields only a
crosshair; invalid supplied size does not get a guessed rectangle.

Pillow decodes a bounded encoded-byte snapshot. The image, metadata and source
identity are bound together, EXIF is applied once, and projection uses the actual
on-canvas image rectangle, excluding letterbox margins. Late results and source
changes are discarded; stale PIL images are closed. The older cache/shared-decode
pipeline is not retroactively assigned source proof, and its strict validated
mapping provider is unchanged.

## What the mark means

It is the reported primary AF area under a documented geometric convention, not
proof of an eye position, actual focus success or the photographer's intended
subject. In subject-detection modes, a search area can have its centre away from
a person. An independent Focus-Points DC-S5M2 portrait screenshot matched to its
original JPEG/RAW supports the full-frame/EXIF8 display convention to approximately
2 pixels of screenshot border width. That does not certify every mode or crop.

The code does not authenticate a JPEG as untouched camera output. Same-size
editing with retained metadata can remain undetectable; use original camera
JPEGs. Source/parser hash and stat checks address ordinary changes, not a fully
atomic cross-process or adversarial guarantee. No record is promoted to a
validated AF-selected person. Single-person quality stays on face/body ROI;
missing AF alone never changes keep/reject/review.

## Dependencies and RAW I/O

ExifTool is optional and found on PATH; the CLI's --exiftool is per invocation,
not a saved UI setting. No executable is automatically installed or bundled.
Metadata extraction does not demosaic RAW. It does read the complete source for
identity hashes. This dedicated display path decodes JPEG pixels only; it does
not add RAW pixel decoding. Actual Windows packaging and real Tk-screen
verification remain separate from headless cloud tests.

## Processing-source protection (adapter 5)

Known external editor software or any nonempty XMP processing history prevents
AF marks until the image geometry is established. /metadata history does not
prove a pixel edit and is not treated as one; it also does not authenticate the
current raster. Shared XMP crop checks reject active, partial, conflicting or
invalid transforms across all supported brands. An explicit complete identity
crop is only consistency evidence. Missing history never proves originality.

This narrows the previously demonstrated Sony A6600 DPReview JPEG: its Adobe
Camera Raw history now hides the marker. Canon's DPReview Lightroom JPEG is also
hidden. Their numeric parsing and historical independent display comparisons
remain evidence of coordinate conventions, not current accepted raster sources.
Panasonic's three tested JPEGs have only camera Software and remain diagnostic.
No subject or quality decision is changed by this guard.

Requested RAW-only and multiple-marker display are subsequent work; this
checkpoint still permits a unique diagnostic marker from a bound JPEG only.
