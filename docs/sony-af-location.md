# Sony ILCE-6600 playback focus-location display

The read-only adapter recognizes the exact SONY / ILCE-6600 profile. Other Sony
models remain unsupported until their own geometry and mode evidence is checked.
No AF field changes subject selection, quality, keep/reject/review, grouping or
saved human decisions. Single-person AF is display-only.

ExifTool's numeric FocusLocation contains [reference width,height,x,y]. The
reference frame must agree with FullImageSize. JPEG drawing additionally requires
6000×4000 stored pixels and matching Sony/EXIF image dimensions; RAW sensor storage
sizes are not used for normalization. Valid EXIF orientation is applied once.
Optional crop/zoom indicators must not contradict the full-frame assumption.

FocusLocation is a camera playback position. Official ExifTool documentation says
the image center can be stored when location information is unavailable. A center
position therefore remains unknown and is not drawn. Presence of a non-center
position still does not establish autofocus success, an eye or intended person.
Manual, direct-manual, unknown or conflicting focus modes do not enable marks.
Recognized automatic modes on this exact profile are AF-S, AF-C and AF-A.

FocusFrameSize has three numeric fields: width, height and validity. Missing size
produces only a crosshair. Invalid or unavailable supplied size is not replaced by
a guessed rectangle. Equal recognized Sony focus-mode instances can agree under
this model's specific definitions; duplicate coordinate/size instances remain
ambiguous even if values are equal. All raw fields remain available for diagnosis.

The existing default-off grouping AF control uses a source-bound dedicated JPEG
preview. Sony's card label says “AF回放位置（非确认焦点）”. The same byte-snapshot,
source/parser identity, pixel binding, stale-result and image cleanup checks apply
as for the Panasonic path. RAW-only files now have the bounded uncalibrated reference-point route described below in this display.
Same-size edited images with preserved metadata cannot be authenticated as
untouched camera originals. ExifTool's separate path read is not an adversarially
atomic metadata snapshot.

The public ILCE-6600 ARW/JPEG reference stores 6000 4000 3928 1700 and EXIF8.
Normalized display coordinates are (0.425,0.3453333333). With the independent
Focus-Points screenshot image rectangle [27,55,634,966], the point is
(284.975,369.598667), within approximately 0.6 screenshot pixels of its reference.
There is no frame-size tag, so only a point is justified. This is evidence of a
display convention, not camera focus accuracy or image sharpness.

Official tag definitions:
https://github.com/exiftool/exiftool/blob/2200871d9cef988051d2a99d67df3bda6cbb30a8/lib/Image/ExifTool/Sony.pm
Independent display reference:
https://github.com/FocusPointsLrC/Focus-Points/blob/7524bff8e4e8cd30ea3ac714397de81dd4142141/docs/images/Sony%205.jpg
Original gallery:
https://www.dpreview.com/reviews/0269606239/sony-a6600-review/

Research photos and screenshots are excluded from the source distribution.
Real Tk and Windows runtime verification remain outstanding in the cloud-only,
headless environment. Optional ExifTool licensing/deployment remains unchanged.

Normalization by W/H follows the independently observed display convention.
Official public evidence does not settle subpixel endpoint W/H versus W−1/H−1;
the screenshot's line width cannot resolve that difference. This limitation is
another reason the result remains diagnostic rather than calibrated geometry.

## Round22: explicitly uncalibrated RAW playback reference

The default-off grouping AF diagnostic switch can now show a **point only** on a
source-bound ILCE-6600 `IFD0:PreviewImage`. The label explicitly says:
“AF回放参考点（未校准）：仅供位置参考，不代表已合焦人物，不用于自动选人或清晰度。”

This narrowly follows an independent A7Info display convention. Its official
manual describes RAW embedded JPEG previews up to1616×1080 and lists A6600
support. The same author's A7InfoLR0.1.5-beta code documents normalized
`x/referenceWidth,y/referenceHeight`, then orientation, with a reference to
A7Info's own preview rendering. This is independent implementation evidence,
not a camera LCD measurement or per-pixel calibration.

Initial allowed profile requires the two observed originals' contract:
-1616×1080 decoded preview and6000×4000 AF/reference/EXIF/Sony dimensions;
-storage6048×4024, crop origin12/12 and crop size6000×4000 in both recorded pairs;
-digital zoom1, auto portrait framing0, distortion setting2;
-AF-C3, AF area0 or14 and setting0; no FocusFrameSize (no rectangle invented);
-EXIF1 or8, applied exactly once; other/mirrored orientations currently hidden;
-no known processing-history conflict and all existing snapshot/parser/pixel binding;
  missing history does not authenticate camera originals.

Distortion setting2 is not treated as Off. The proposed display is already in
camera-rendered preview coordinates, with no inverse correction or guessed
padding. Modes/directions outside the initial profile are unsupported here,
not declared mathematically invalid.

The old direct correspondence check's AF-local p95 exceeded its declared3px
criterion. That failure is retained. A separate patch comparison suggests a
large descriptor mismatch, but neither it nor this independent implementation
retroactively makes that test pass. The new point therefore remains explicitly
uncalibrated.

A separate display-only reason prevents this1616px source from entering the
native RAW person/quality pipeline or being mislabeled as Nikon last-AF data.
No Sony automatic subject selection or quality decision is enabled.

Primary independent references:
https://www.soens.de/a7info-help.html
https://www.soens.de/downloads/A7Info_En.pdf
