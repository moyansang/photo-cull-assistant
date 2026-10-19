# Same-original JPEG AF-area diagnostic

This is an explicit offline diagnostic. Its same-source preparation function
also supplies the bounded group-page reference preview; it is separate from all
subject/quality decisions. It uses pixels and AF metadata from the same
original JPEG, preserves the full image frame, applies EXIF orientation once,
and records the exact source and output identities. It does not infer a RAW
crop from a paired JPEG filename or use an unproven cached preview.

The diagram represents the camera-reported primary AF area under a geometric
interpretation. It is labelled UNVALIDATED and must not be treated as proof of
an eye location, actual autofocus success, photographer intention or sharpness.
In subject-detection modes the reported area can be a region in which a subject
is sought. Its centre need not lie on the visible person's face.

The original JPEG is not edited. Output paths must be new. Report evidence
includes source SHA/stat, parser version, stored and EXIF-transposed dimensions,
no-crop transform provenance and image hashes. Only supported valid evidence
produces diagnostic marks; unknown/conflicting evidence is not guessed.

## Evidence boundary

Public DC-S5M2 pairs 01, 09 and 45 establish metadata availability and same-shot
RAW/JPEG agreement. Sample09 is orientation8. Sample45's unchanged reported area
centre falls on background to the right of the main person; it is not moved to
match a presumed subject. These observations are not a focus-accuracy benchmark.

The project does not redistribute these all-rights-reserved photographs or their
annotated derivatives. Source packages contain code, tests and numeric evidence,
not sample images. The group-page frame provider remains protected and no
validated display mapping or automatic AF-selected person is created here.


Run from the source checkout:

```sh
PYTHONPATH=src python -m ai_cull_assistant.af_diagnostic original.jpg \
  --report new-report.json --preview new-diagnostic.png \
  --exiftool /trusted/path/to/exiftool
```

Both outputs must be new files. JPEG/MPO input uses only the primary JPEG frame.
RAW input permits an information-only report with no image; no sibling JPEG is
guessed. The decoding snapshot is bounded to 256 MiB and normal Pillow pixel
limits; this inspector is not a general unlimited-image decoder.
