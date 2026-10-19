# Canon EOS R3 AFInfo2 display

This profile is limited to Canon EOS R3. It reads file-specific AFInfo2 values
through the fixed read-only ExifTool interface. Other EOS cameras, PowerShot
coordinate systems, older AFInfo records and guessed layouts are unsupported.

AFImageWidth/Height define AF coordinates. EOS coordinates have a central origin,
positive X right and positive Y up. AFInfo2 arrays must have NumAFPoints entries;
ValidAFPoints is a count, not a documented universal prefix rule. This profile
checks all positive-size rectangles and their count. Unused zero-size entries
cannot be selected; malformed sizes or conflicting counts remain unknown.

With ExifTool -n, AFPointsSelected and AFPointsInFocus contain signed16-bit words,
not already-decoded point indices. Each word is decoded with 16 bits. Counts,
padding, indices, all parallel arrays and referenced rectangles are checked.
Selected points and camera-reported in-focus points stay separate. Every valid
active record is retained for source-bound diagnostic display, with its own role.
Multiple records leave singleton fields unset and never choose one as a subject.
The display resource cap is4096 active records; larger collections are preserved
in metadata but hidden whole. PrimaryAFPoint is not silently substituted.
See multiple-af-records.md for roles, provenance and validation limits.

Canon and CR3 Track4 copies may agree only after field-specific valid-value
checks. All original instances remain in the evidence; conflicts fail closed.
Known automatic EOS modes are interpreted separately from manual/unknown modes.
Crop, zoom, XMP crop and required image/AF dimensions must be consistent with
full-frame JPEG display. No AF geometry is transferred between RAW and JPEG.

The public same-shot fixture illustrates why source binding matters: JPEG center
x457/y831 differs from CR3 center x449/y817. Both use 6000×4000 AF coordinates and
150×150 area. The difference's cause is unknown; no compensation is inferred.
The independent screenshot comparison (maximum border difference 1.19px) applies
to the JPEG only. That JPEG contains Lightroom software/history, even though the
publisher calls it out-of-camera and HistoryChanged says /metadata. We cannot
prove pixels changed or stayed geometrically unchanged. The current shared
processing guard preserves its AF metadata but suppresses automatic marks.

A second PhotographyBlog R3 pair has matching x1908/y-1076, 432×432 area,
FocusMode0 and EXIF8. Neither file has observed editor/history tags; its own
JPEG successfully exercises the orientation-aware diagnostic display. There is
no independent focus screenshot for that file. Absence of processing history
still does not authenticate an untouched original.

The grouping page uses its existing default-off AF control. It labels Canon
marks AF记录点（非清晰度证明） and distinguishes selected versus camera-reported
in-focus status. No camera success flag is an optical sharpness measurement.
All AF automatic subject/quality decision flags remain false; missing AF alone
does not change keep/reject/review. Single-person quality stays face/body based.

Only the real fixtures' observed modes/orientations/one-active-point cases are actual-camera
checked. Additional directions, negative cases and mask patterns are synthetic
schema tests. Original-photo authenticity, real Tk interactions and Windows
execution are not certified. Same-size edited files with retained metadata may
not be detectable. RAW-only overlay remains unsupported in this JPEG display path.

Official definitions:
https://github.com/exiftool/exiftool/blob/2200871d9cef988051d2a99d67df3bda6cbb30a8/lib/Image/ExifTool/Canon.pm
Independent display:
https://github.com/FocusPointsLrC/Focus-Points/blob/7524bff8e4e8cd30ea3ac714397de81dd4142141/docs/images/Canon%203.jpg
Public gallery:
https://www.dpreview.com/samples/8471987995/canon-eos-r3-sample-gallery/

Research photos/screenshots are excluded from the source distribution.
