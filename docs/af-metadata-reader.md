# Read-only camera AF metadata reader

This optional inspector reads camera-reported autofocus metadata. It does not
choose people, declare a face sharp, change keep/reject/review, write camera files,
change groups, or activate the experimental visibility models. No GUI layout changes.

## Run

Install the application source package as usual. Install ExifTool separately from
its official source; no tool download occurs during inspection. Then use:

```
aicull-af original.RW2 original.JPG --exiftool /trusted/path/to/exiftool --output new-report.json
```

From the source tree, the equivalent is:

```
PYTHONPATH=src python -m ai_cull_assistant.af_inspect original.RW2 --exiftool /trusted/path/to/exiftool
```

JSON goes to stdout unless `--output` specifies a NEW file. The CLI refuses to
replace an existing report or input. It accepts a bounded timeout for each parser subprocess (version query and metadata read); source hashing is outside that subprocess timeout.
Missing ExifTool is a reported error, not an automatic installation or an implicit
camera/metadata failure. For a normal installation the `aicull-af` console entry
is registered in pyproject.toml. The existing Windows GUI executable builder is
unchanged; it does not acquire an AF inspector screen or bundle ExifTool.

## Evidence and limits

ExifTool documents Panasonic AFPointPosition as the **primary AF area centre**,
not every point that attained focus. AFAreaSize is relative size; AFAreaMode,
FocusMode and AFSubjectDetection provide context. Raw group-qualified fields are
retained. A centre can lie on a different person after focus/recompose, a broad
area can overlap people, and AF selection does not establish image sharpness.

Statuses distinguish missing fields, unsupported cameras, parse/tool/source
errors, and parsed-but-not-validated Panasonic evidence. No successful parse is
labelled camera-validated. Mathematical EXIF-direction conversion is diagnostic
only: RAW sensor margins, crop/aspect ratio, embedded JPEGs, camera firmware and
mode-specific AF coordinate conventions require original-camera verification.
Unknown transforms and modes stay unvalidated. Multi-person association is absent.

At the initial AF-reader release, no original DC-S5M2 RAW/JPEG comparison set
was available. Tests for its reported
Model string are mock contract tests, not proof of S5M2 real-world correctness.
Public photos lacking AF metadata stay unknown; they cannot be filled from the
largest or sharpest detected face. No actual Windows execution was performed.

## External dependency and licence record

Cloud smoke uses official ExifTool13.59, source commit
2200871d9cef988051d2a99d67df3bda6cbb30a8. Download URL:
https://codeload.github.com/exiftool/exiftool/tar.gz/2200871d9cef988051d2a99d67df3bda6cbb30a8

Archive SHA256:
e1e2ad6c6fbf568afee5993ef8b2b91ab013d21698c9304e079e633ad82776f5

The pinned README states Copyright2003–2026 Phil Harvey and distribution on Perl's
terms (Artistic or GPL); the repository LICENSE file contains GPLv3 text. Both
unaltered records are retained with the test evidence. Do not describe ExifTool
as MIT or silently relicense it under this application's PolyForm licence. This
change neither vendors ExifTool code nor redistributes its executable inside the
application. Windows executable packaging/licence notices require a separate
verified packaging step if later requested; Python ExifTool wrappers alone do not
supply the external executable.

Official references:
- https://exiftool.org/TagNames/Panasonic.html
- https://github.com/exiftool/exiftool/tree/2200871d9cef988051d2a99d67df3bda6cbb30a8

The parser is invoked without a shell, with fixed read-only arguments, explicit
absolute file paths, user configuration disabled, and Perl injection variables
removed. Raw originals are never edited. Original file identity and parser
identity are recorded; no reuse of stale metadata across changed files is assumed.

The 1 MiB JSON limit is checked after subprocess output is captured; it is not a
live memory cap. The timeout bounds each tool subprocess, not file hashing or
the combined two-process operation. Inspection is intended for trusted local
camera files and a trusted separately installed executable.

## Initial AF-reader verification (historical)

119 mocked/unit CLI tests cover normalized EXIF orientations 1–8, invalid and
sentinel values, duplicate/conflicting fields, no-tool and timeout errors,
Unicode paths, read-only argv, source/parser changes and uncached rereads.
Official ExifTool test fixtures: DMC-FZ3 JPEG gives `missing`; DMC-LX3 RW2 gives
`not_validated`. A Chinese-path copy of that RW2 also parses. All three fixtures
and all 26 available public pilot photos retained identical SHA256 after reading.
Of those 26 photos, 21 are `unsupported` by the Panasonic interpretation; 5
return `error` because ExifTool reports metadata warnings (including minor
MakerNote/XMP repairs). These warnings are deliberately not silently accepted,
and do not mean the photos are visually bad or lack AF information. The LX3
fixture has conflicting thumbnail/RAW/EXIF dimensions; these are preserved and
no pixel coordinate frame is selected. These are parser checks, not S5M2 tests.


## Adapter version 2: agreeing orientation instances

Three public same-shot DC-S5M2 JPEG/RW2 pairs subsequently supplied real parser
evidence. Their RAW main-IFD orientation instances agree (1, 8, 1). Version 2
normalizes only identical valid orientation semantics, retaining every raw field
and the main-frame source list. Conflicting, invalid or unknown orientation
values remain unsafe; no thumbnail direction fills a missing main direction.
Duplicate AF positions/areas are still not merged. This changes orientation
interpretation, not camera-coordinate validation or autofocus subject selection.
See af-jpeg-diagnostic.md for the separate, explicitly unvalidated diagnostic.

## Adapter version 3: scoped Sony interpretation

Sony ILCE-6600 now has a separate read-only playback focus-location profile and
same-JPEG display path. This adds fixed read tags, exact model/field rules and
cache-version invalidation without changing quality decisions. Other Sony models
remain unsupported. See sony-af-location.md for focus-mode, dimension, optional
frame-size validity and center-fallback limitations. Earlier pilot statistics
above describe the original Panasonic-only adapter and are not rerun claims.

## Adapter version 4: scoped Canon AFInfo2 interpretation

Canon EOS R3 adds signed-word AF bitmask decoding, separate selected/camera-reported
in-focus evidence, strict arrays/counts and source-specific geometry. A unique
unambiguous point may be shown through the same-JPEG preview; multiple different
points remain metadata-only. RAW/JPEG centers in the public pair differ and are
never substituted across files. See canon-af-records.md. No quality policy changes.

Adapter version 5 also retains Software, CreatorTool, HistorySoftwareAgent,
HistoryAction, HistoryChanged and DistortionCorrectionSetting in the same
read-only invocation. These fields support provenance inspection; none proves
that an image is sharp or that its pixels were edited. Known processing history
can disable a preview mark without changing its retained AF records or quality.
