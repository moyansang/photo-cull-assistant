# Source-bound RAW embedded-preview diagnostics

RAW-only group preview does not need a paired JPEG. The optional AF toggle uses
an explicitly profiled embedded camera JPEG from the selected RAW. It does not
perform a full RAW pixel decode. A paired JPEG, when already explicit in the
asset, retains existing priority; no filesystem sibling search is added.

## Source and pixel binding

The worker makes a bounded private RAW snapshot (512 MiB cap) and verifies source,
snapshot and parser identities. AF metadata is read from that snapshot; recorded
metadata paths remain truthful historical temporary paths, linked to the original
by matching content SHA256. Named-tag extraction uses fixed read-only arguments,
a stripped environment, timeouts and streaming stdout/stderr caps. Temporary
snapshots are removed on success/failure. Extraction can consume up to64 MiB and
JPEG decoding up to64 million pixels; detached UI raster is at most768 pixels
on its long edge. No original source write or persistent extracted image occurs.

The existing AF metadata subprocess captures output before its1 MiB post-check;
its separate retained limitation is not described as a streaming hard cap.
Metadata, extraction and JPEG decode are bounded separately, not by one global
wall-clock timeout. No Windows execution or actual Tk interaction is certified.

Embedded EXIF orientation must agree with the RAW main orientation. If absent,
the validated RAW main orientation is used. It is applied once before downscaling;
conflicting, malformed or duplicate embedded orientation remains unsafe. Source,
parser, extraction tag, full encoded SHA, snapshot linkage, clean raster SHA and
orientation/crop evidence are checked before sealing. Fast switching, page
unmapping, toggle-off and late results retain the existing cleanup/stale guards.

## Bounded model capabilities

- Canon EOS R3: Track1:JpgFromRaw. The initial AF diagnostic profile requires a
  native6000×4000 preview and matching camera/AF/EXIF frame, explicit zero crop
  margins/offsets and zoom, full cropped dimensions, correction setting0, and
  unambiguous AF and orientation evidence. This displays the RAW's own record.
- Panasonic DC-S5M2: IFD0:JpgFromRaw2. Native large preview is viewable. One measured
  file has272 opaque trailing bytes after the primary JPEG. The extraction keeps
  their original bytes/hash and reports the primary/trailer lengths and hashes;
  semantics remain unknown. Only this profile allows a bounded trailer (512-byte
  cap), never another JPEG SOI/concatenated image. AF mapping remains unknown.
- Nikon Z5_2: SubIFD:JpgFromRaw is viewable. Its6048×4032 output frame differs from
  the6064×4040 sensor storage. Version0402 AF diagnostics now use this output
  frame with explicit native CropArea8,4,6048,4032 and zero retouch/distortion.
  No sensor-border offset is added. See nikon-af-metadata.md.
- Sony ILCE-6600: IFD0:PreviewImage is viewable. The measured1616×1080 preview does
  not share the6000×4000 AF aspect ratio. Round22 follows the independently
  documented normalized playback-reference convention on an exact bounded
  profile, with an explicit uncalibrated point label. No padding/crop/warp is
  inferred, and no subject/quality authority follows. See sony-af-location.md.

Other models/preview tags have no blanket support. An available camera preview
and a supported AF mapping are separate capabilities. The UI can show the former
with a precise unavailable-AF status. The camera-rendered preview's compression
and resolution are not full-RAW sharpness truth.

## Meaning and pending work

Canon's and Nikon's displayed RAW AF records are uncalibrated coordinate diagnostics, not
proof of successful focus, a visible eye, selected person or sharpness. Dimensions
alone are not physical mapping validation; explicit native crop/correction checks
only bound the hypothesis. Missing AF never changes quality. Single-person
face/body and manual decisions remain unchanged. No automatic AF subject or
keep/reject rule is enabled.

Canon multi-record display now preserves all valid active selected/reported
records; see multiple-af-records.md. No point is promoted to a quality subject. Actual
source/frame execution, independent projection tests and no-source-write checks
must not be described as camera-LCD or target-identity ground truth.

Round22 update: the bounded Sony A6600 profile now supports an explicitly uncalibrated playback-reference point through the existing default-off diagnostic switch. See sony-af-location.md for the exact source/mode/orientation gates and independent evidence; automatic subject/quality authority remains unavailable.
