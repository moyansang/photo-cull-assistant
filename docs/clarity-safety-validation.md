# Cloud build17 safety patch validation (2026-10-18)

## Provenance and scope

Starting code: clean, sanitized build17 source export of original main commit
`8a70f58e450499e869eb9c4976c49e683548a25e`, tree
`97f2367e46a5e271f5309870df85732fd31b4759`. The cloud repository's baseline
`4961e9d` is a new snapshot, **not the original Git history**. Supplied tracked
files were preserved; four separately downloaded official model assets were
SHA-256/size verified and excluded from this patch. No original photos, local
computer, production workspace, paid AI, push, release, EXE build or installation
was used.

## Implemented

- Face sampling records exact affine/crop source support. Reflected or clipped
  regions cannot provide supported quality evidence.
- Five geometric landmarks and weak texture do not establish visible eyes.
  Visibility is recorded as unknown, not inferred from landmarks. Existing
  supported severe-face thresholds and rejection behavior are preserved.
  Invalid sampling, insufficient native pixels, contrast and noise safeguards
  can withhold a decision; unknown visibility alone does not disable face-only
  rejection. Semantic eye-covering protection is **not solved**: this model and
  dataset do not supply a validated eye-visibility assay.
- An initial experimental global face-severe-to-review downgrade was rejected
  during review and removed before the final candidate. It is not the delivered
  default or a claimed safety improvement. Original severe-face positive
  regression expectations were restored. No quality thresholds were relaxed.
- Persisted part-specific face/body reasons and policy `reasons_by_part` keep
  selection, face, body and measurement failures separate. Original records are
  retained. Early pose/association failures retain their specific code.
- All initial YuNet face candidates plus the chosen rescue, source, score and
  geometry are recorded (not every head-detector/retry candidate). A rescued
  face cannot reuse the initial detector's single-candidate proof. Multi-person
  intent remains unresolved: no largest-face or central-face automatic bypass,
  no inferred background/screen/person exclusion.
- Body ROIs record actual measurement size, coordinate size, origin, bounds,
  geometric association and unknown visibility/occlusion. Competing-person
  body envelopes veto contaminated ROIs. Manual crop results are rechecked
  against the full image so an outside-crop neighbor cannot be hidden.
- This is geometric support based on detected candidates, not pixel-perfect
  instance ownership. Missed people, inaccurate pose envelopes and semantic
  occlusion remain limitations. Broad envelopes may over-block close people.
- Unsafe/low-texture regions remain insufficient and cannot be dropped to turn
  a body clear. Reliable torso-plus-another-family severe evidence is retained.
- Decode summaries distinguish unrecorded operations from short measured times.
- Group page layout/evidence changes are described separately in
  `grouping-layout-validation.md`. Grouping algorithm, group members, save
  semantics, manual decision priority and automatic `human_confirmed=false`
  remain unchanged. Derived evidence versions are invalidated; old measurements
  are never silently re-signed. Paid evidence cache identities are not rewritten.

## Public fixture execution, not portrait accuracy

Both versions successfully executed all 42 downloaded/verified images. Fixed
splits were retained and no threshold was changed after viewing holdout outputs.

- 18 COCO images, both face-only and face+body modes: 18 review before and after.
  Of these, 11 have multiple detected faces and 1 has no detected face. Six have
  automatic range: four insufficient native face size, one insufficient eye
  pixels, and one borderline detail plus insufficient source support. These are
  observed blockers, **not proof that the review decisions are correct**.
- Body detector produced 19 candidates in both runs: 18 clear/1 uncertain became
  7 clear/12 uncertain. Eleven changed candidates: eight include competing-person
  envelope overlap, three are blocked by low texture or insufficient subject
  pixels. Per-image candidate boxes, unsafe regions, measured states and recorded
  competitor envelopes are preserved in `public-comparison.json`. No quality
  ground truth is available to score the benefit or over-blocking rate.
- COCO 438304 is a photographed advertisement; it remains separately identified
  in the dataset documentation. The detector does not identify printed people.
- RealBlur: median native tile normalized-Laplacian reference>blur ordering is
  6/6 development pairs and 5/6 held-out pairs in both versions. The failed
  holdout is scene178/pair1 (reference 0.1123581, blur 0.1397416). Holdout has only
  two scenes/six pairs. The score is an unchanged diagnostic; these static scenes
  have no people and do not validate portrait/eye/limb quality or a keep decision.
- Full per-image evidence: `results/public-baseline.json`,
  `results/public-improved.json`; comparison: `results/public-comparison.json` in
  the accompanying cloud validation bundle. Scripts under `scripts/` reproduce
  these runs with PYTHONPATH set separately to each version.

## Tests and environment

- Focused regression: 191 passed, including native face/body model loading,
  candidate provenance, manual/source/cache contracts, grouping, coordinate
  round trips, unsafe ownership veto, low texture, positive separate-neighbor
  evidence and reliable multi-family severe controls.
- Initial unrestricted baseline run: 1277 passed, 149 failed, 40 errors, 24
  skipped. Initial experimental modified run (before final scope correction): 1308 passed, 151 failed, 40 errors, 24 skipped.
  These are **not successful full-suite runs**. Baseline failures comprised 183
  display-dependent cases, five Windows updater `st_file_attributes` cases,
  and a missing optional rawpy dependency. rawpy was subsequently installed.
  The modified run exposed two additional integration fixture mismatches, which
  were investigated rather than classified as environment failures.
- The manual-body fixture puts a person at each crop center but a different
  full-image location. Its former clear result correctly becomes review under
  the ownership check. Its expectations now require the explicit ownership
  blocker while retaining original-photo hashes, saved state, unchanged-photo
  evidence and no-paid-API assertions.
- The group-viewer stub must implement the newly added view-filter method; its
  exact double-click member/group ordering assertion is retained.
- Final narrowed candidate headless regression: **1327 passed, 24 skipped,
  188 deselected**; baseline with the same environment exclusions and optional
  dependency fix: **1278 passed, 24 skipped, 188 deselected**.
- Final headless regression excludes an explicit, recorded list of 183
  display-dependent cases and five Windows-only cases. See accompanying logs
  and `environment-blocked-tests.json`; exclusion is not a passing result.
- Tk cannot initialize without DISPLAY. Xvfb could not create sockets under
  this execution sandbox; the requested escalation also failed. No workaround
  through the user's computer was attempted. No new GUI screenshots or Windows
  native/EXE acceptance are claimed. The 24 self-skipped cases are not counted as passes.

## Acceptance status

Implemented and tested: invalid-sampling safeguards, cause separation, conservative
body competition checks, provenance and contract regression. **Not established:**
real-user false-reject reduction, false-keep rate, correct subject selection,
accurate semantic occlusion recognition, or reduced unnecessary review. The
original 1000-image/799-review workspace was not available in the cloud and was
not reproduced. Independent eye/limb/intent annotation, dangerous real examples,
and native GUI/Windows validation are still needed before a product release.
