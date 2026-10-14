# Manual portrait and landscape analysis

Development implementation and research reviewed 2026-10-13. No model weights were
added or replaced, no automatic scene classifier was added, and the existing
project and third-party attribution files are retained.

## Source and license review

- [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet)
  provides face rectangles and five actual landmarks. Its
  [model license is MIT](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE).
  Reuse the shipped `2023mar` ONNX and existing loader; preserve
  `data/YUNET-SOURCE.txt` and `data/YUNET-LICENSE.txt`.
- [OpenCV Zoo person detector](https://github.com/opencv/opencv_zoo/tree/main/models/person_detection_mediapipe)
  gives a detector rectangle and body anchors. Its
  [pinned Apache-2.0 license](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/person_detection_mediapipe/LICENSE)
  is already shipped. The detector rectangle is not treated as a full-body box.
- [OpenCV Zoo pose estimator](https://github.com/opencv/opencv_zoo/tree/main/models/pose_estimation_mediapipe)
  produces 33 keypoints and a segmentation mask. Its
  [pinned Apache-2.0 license](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/pose_estimation_mediapipe/LICENSE)
  is already shipped. Reuse the existing inference wrappers and validated model
  hashes from `body_models/manifest.json`; runtime does not download models.
- [OpenCV](https://github.com/opencv/opencv) supplies the existing image-gradient,
  filtering and DNN operations under its
  [Apache-2.0 license](https://github.com/opencv/opencv/blob/4.x/LICENSE).
  The new aggregation, whole-frame texture gating and UI logic are original
  project code. No third-party application source was copied, and no AGPL/GPL
  model or new inference dependency was introduced by this module.

## Integration contract

`screen_assets(..., analysis_mode='portrait'|'landscape')` routes to
`analysis_modes.assess_asset_mode`. An omitted mode preserves the legacy public
screening API for older integrations; the new scan workflow passes its explicit
workspace selection. The mode is selected before scanning. This module never
classifies a photo's scene or introduces a per-photo switch.

The existing `ScreeningResult` shape is retained. `focus_evidence` contains:

- `mode`, `version`, `state` (clear/severe_blur/uncertain)
- `reliability` (adequate/insufficient), distinct from the quality state
- `recommendation` (keep/reject/review), never a star rating
- `subjects`, preserving each person's separate result
- `body_candidates` and `visible_boxes` for portrait body display
- `eye_evidence`, with `eyes_unassessed` on body fallback

`clarity_version='clarity-v2'` remains the persistence envelope accepted by the
existing readers. The nested mode version is `manual-modes-v1`; the face cache
version changes to `native-face-v5-jpg-first` and keys both members of RAW+JPG
pairs. Native pixel deficits and actual contrast/artifact insufficiency can request
one full-RAW recheck; borderline quality or close group scores cannot. If RAW
refinement fails or remains insufficient, the result stays review.
Opening an old session does not re-run analysis by itself.

`recommend_group(assets)` returns tied keep suggestions without mutating manual,
AI or reject decisions. All adequately supported keep candidates may tie;
non-winners are not rejected. No numeric-to-star mapping is present.

## Portrait safeguards

Real face/eye evidence has priority. A borderline visible face cannot be hidden
by a sharp body result. When face evidence is unavailable, a supported pose
locates the body and the existing segmented body-region checks assess its detail.
The full-body display rectangle comes from visible major joints plus the head
extent, not from relabeling the detector's head rectangle. An unsupported pose
or unavailable body model leaves the photo for review, rather than fabricating a
body or rejecting the image. Multiple automatic bodies require subject selection.

`CropSettings.photos[key]['selected_bodies']` stores normalized xywh rectangles;
an explicit empty list means the user ignored all bodies. Existing selected-face
fields continue to work. Faces and bodies may both be selected. Mixed-quality
people yield review and retain individual issues; no average hides one person.
Reject is only a recommendation, never deletion of source files.

The subject page's “人体主体” control opens a preview-only editor with automatic
candidates, manual drag boxes, individual selection/removal, ignore-all, and
restore-auto. Applying edits only changes the draft; the existing Save/rescan
flow handles local recomputation. The main preview shows purple B1/B2 body boxes
and separate body-result labels that explicitly say eyes were not assessed.

## Landscape safeguards and calibration limit

Landscape runs whole-frame internal tiled checks without face detection and
without visible or user-drawn boxes. Low-texture tiles such as sky, water and
plain areas are ineligible as blur evidence. A supported sharp region can coexist
with intentionally defocused regions. If detail is unresolved the result is
review, not an automatically bad photograph.

**Landscape negative classification is intentionally not calibrated. This
version returns keep/review only for landscape and does not claim a complete
landscape rejection classifier.** Tile count, tile size, pose thresholds and
inherited focus cutoffs are conservative development constants, not validated
universal photographic-quality thresholds. No camera/RAW-format or real-photo
accuracy claim follows from the synthetic regression tests.

## Verification

Synthetic and mocked-inference tests cover manual-only modes, no-subject review,
face-first processing, body boxes from actual pose extent, missing-pose rejection
of a candidate, body-versus-eye evidence separation, multi-person disagreements,
explicit ignored bodies, tied group recommendations, low-texture landscape
safety, headless editor pointer actions, and per-body labels. Existing native-face,
body-model and subject-landmark tests are also run. Regression coverage also
verifies that stale AI participant labels use fresh local evidence, landscape
never enters portrait-relative comparisons, and relative concerns cannot leave
a stale keep recommendation.

At the initial implementation check, the cloud shell has no DISPLAY and no
xvfb-run, so Tk screenshot/layout tests require a display-enabled runner. This is
a verification limitation, not evidence that GUI tests passed. Real photos,
landscape negative calibration, small/profile subjects, occlusion and multiple
camera variants remain validation work before production distribution.


## Final scoped acceptance (2026-10-13)

- Implemented and wired: actual body pointer/apply handlers feed CropDialog draft
  settings, App save marks only edited photos, and ProcessingJob rescans only
  those photos. `test_subject_flow_acceptance` executes that chain with the real
  body-region measurements and session persistence; model pose outputs are
  controlled. It checks per-body clear/uncertain results, stale AI history,
  unchanged neighboring evidence, no paid calls, and unchanged source hashes.
- Fixed during deeper acceptance: face plus body of the same person no longer
  counts as two people when a unique pose-head/face geometric correspondence
  supports a link. Body evidence remains separately visible. Conflicting body
  quality does not silently disappear behind a clear face. Ambiguous overlap
  stays review; containment alone does not prove the same person. Reverse
  conflicts (several bodies matching one face) also stay unresolved/review, so
  adjacent or overlapping participants cannot be silently merged.
- Both selected_faces and legacy/manual_face cooperate with selected_bodies.
  An explicitly body-only selection does not silently add an unrelated automatic
  face. It also does not automatically promote a newly visible face inside that
  body box; the user can add/select that face explicitly.
- Valid AI body labels are matched by geometry/stable subject ID, not mixed-list
  position. Stale or unmatchable AI cannot overwrite a local body judgment.
  Body edits mark dependent face labels pending until rescan.
- No Tk rendering claim: the real event handlers/callbacks are driven by headless
  widget substitutes. Actual display, mouse grabs, DPI and Windows layout remain
  unverified. Detector recall/precision and association thresholds still need
  real-photo validation. Landscape rejection remains partial as stated above.
