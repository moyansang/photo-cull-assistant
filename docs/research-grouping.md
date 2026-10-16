# Workspace-wide similarity grouping

Implemented 2026-10-13; bounded crop fallback added 2026-10-14; conservative
portrait context added 2026-10-16. Algorithm version:
`workspace-dhash64-scene-pose-guarded-v1`.

## Research and reuse decision

Reviewed official project sources/documentation before implementing:

- [ImageHash](https://github.com/JohannesBuchner/imagehash),
  [its license](https://github.com/JohannesBuchner/imagehash/blob/master/LICENSE)
  and [package dependencies](https://github.com/JohannesBuchner/imagehash/blob/master/setup.py):
  BSD-2-Clause; provides luminance hashes including dHash and pHash. pHash
  introduces SciPy, and other algorithms add PyWavelets. The existing project
  already has a 64-bit horizontal dHash implementation, so it remains the hash
  family, with explicit LANCZOS resampling and a new algorithm version.
- [scikit-image SSIM documentation](https://scikit-image.org/docs/0.24.x/api/skimage.metrics.html#skimage.metrics.structural_similarity),
  [implementation](https://github.com/scikit-image/scikit-image/blob/v0.24.0/skimage/metrics/_structural_similarity.py)
  and [license](https://github.com/scikit-image/scikit-image/blob/v0.24.0/LICENSE.txt):
  BSD-3-Clause for the main project, with individual third-party notices in the
  license file. Useful reference for explicit data range and covariance settings.
  The current implementation uses the standard SSIM formula on non-overlapping
  blocks, not the scikit-image sliding-window or Gaussian variant. It therefore
  must not claim numerical equivalence to `structural_similarity()` defaults.
- [Ben Hoyt's pybktree](https://github.com/benhoyt/pybktree) and
  [its MIT license](https://github.com/benhoyt/pybktree/blob/master/LICENSE.txt):
  useful reusable Hamming-distance index. Its documentation correctly notes that
  large search radii can degrade to linear searches. A small project-specific
  iterative BK-tree avoids a new packaging dependency and combines identical
  hashes in a single node.
- SSIM's mathematical source is Wang, Bovik, Sheikh and Simoncelli,
  [Image Quality Assessment: From Error Visibility to Structural Similarity](https://ece.uwaterloo.ca/~z70wang/publications/ssim.pdf)
  (IEEE Transactions on Image Processing, 2004).

No code was copied or vendored from these projects. Their names and links are
retained here as attribution for research. No new dependency or third-party
license text is required by this implementation. Existing repository license
and upstream bundled notices remain unchanged. If a later change copies or
vendors an implementation, its full original license/notices must accompany it.

## Scope and matching rule

- Only photos in the supplied current workspace asset list are candidates.
  There is no capture-time, adjacency, directory, or filename proximity gate.
- Input order stays intact. Fresh groups are built in stable source-identity
  order, making the same workspace deterministic even if the input is shuffled.
- Fresh grouping connects pairs passing the unchanged strict whole-frame/crop
  rules. Additional portrait edges can merge strict components only when every
  cross-component pair passes the portrait evidence rule below. A weak A-B /
  B-C chain cannot override an incompatible A-C pair. Existing positive
  memberships retain the legacy fixed-representative path. The representative
  source ID is persisted in `PhotoAsset.grouping_anchor_id` and automatic
  stores' `group_anchors`, so a newly imported earlier filename cannot shift
  the representative between scans. If that representative disappears, the
  group keeps its members but does not automatically gain new ones.
- Both the 64-bit dHash distance and grayscale block-SSIM threshold must pass.
  Aspect ratio must also remain compatible so resizing does not disguise a
  portrait/landscape or major framing change.
- Existing reliable cached face/body appearance can veto a pose mismatch. No
  face/body detector is invoked here, and missing detections alone cannot veto.
- A missing/broken visual or an asset marked `decode_error:` stays singleton.
  Placeholder previews are never treated as real image evidence. No RAW
  decoding, AI requests, ratings, rejection flags or original-file deletion
  happen in the grouping module.

Presets are initial engineering defaults, **not benchmark-calibrated thresholds**:

| Preset | Maximum dHash distance | Minimum block SSIM | Maximum aspect ratio quotient |
| --- | ---: | ---: | ---: |
| strict | 8 / 64 | 0.86 | 1.08 |
| standard | 10 / 64 | 0.78 | 1.15 |
| loose | 12 / 64 | 0.68 | 1.25 |

The grayscale descriptor is EXIF-oriented, resized to 64×64, and divided into
64 non-overlapping 8×8 blocks. SSIM uses population moments, data range 255,
K1=.01 and K2=.03. Scores are averaged across blocks. This is a similarity
feature, never a measure of technical quality or a reason to discard a photo.

### Bounded central-crop fallback

Whole-frame matching is tried first with the unchanged preset. If it fails,
compare the whole frame on one side against a 2.5% or 5% central trim on the
other. Never compare two trims. The original aspect and subject gates remain;
the aligned hash distance is at most 8 and block SSIM at least .86 (or the
configured stricter values). A trim retains at least 81% of the frame area.
Variants come from the same oriented snapshot, reduced to a maximum 512-pixel
edge before trimming. Tiny images do not receive variants. There is no
translation, arbitrary crop, rotation or representative-changing search.
Saved memberships are not silently regrouped when the algorithm version changes.

### Corroborated portrait context

This separate fallback requires one automatic face and one automatic body ROI,
unambiguous selected primaries, the explicit ROI feature schema, and a stable
prepared preview whose encoded SHA-256 matches the saved descriptors. Manual
ROIs, stale/incomplete fields, decode errors, and ambiguous selections never
enter it. It does not modify persisted descriptors or confirmation digests.

Standard matching requires face displacement <= .06, area quotient <= 1.25,
face dHash <= 8 and block SSIM >= .65, plus matching HSV colour distributions.
A broad **face-anchored body context** must also pass dHash <= 10 and colour
comparison. This is appearance evidence, not skeletal pose or person identity.
An unbound or unstable explicit body box is neither positive proof nor a veto;
its original hash and the original strict path stay unchanged.

Scene proof comes from ORB keypoints outside the face-anchored context and all
body candidates. Every rotated, scale-dependent descriptor patch must remain
outside those masks, not just its centre. Partial affine RANSAC requires at
least 12 inliers, .55 inlier ratio, distributed points in both frames, scale
.94..1.06, rotation <= 3 degrees, and centre displacement <= .06 of the frame.
Warped common background must cover >= .12 of the frame and have RGB MAE <= .04.
Low-texture backgrounds and narrowly clustered matches stay separate. Strict
uses narrower bounds; loose does not weaken these portrait safeguards.

An independent body-ROI drift path permits only a phase-correlation proposal
within 3% translation and response >= .80. It requires conflicting old body
hashes with genuinely changed automatic rectangles, body IoU >= .60, unchanged
whole-frame hash limits, face SSIM >= .80, and face-context SSIM >= .65. The
aligned whole frame **and the full union of both body rectangles** must meet
the existing strict-crop SSIM reference .86, plus body colour agreement >= .80.
Comparing only the intersection could hide changed clothing at a rectangle's
edge; every available union pixel contributes. Face and body are independent
appearance witnesses, never assumed to be the same person's confirmed ROI.

The proposal then needs independent common-background proof, excluding expanded
hair/body-top masks and full interpolation support. Common area >= 12% and RGB
MAE <= .04 remain required. Only actual unmasked pixels enter 16-square cells
(>=32 pixels and standard deviation >=8 on both sides). At least eight cells
and 512 textured pixels are required. Their area-weighted median SSIM must be
>= .78 and >=60% weight must meet .70. Matching support centroids must span half
of each axis with convex hull area >= .10. Thin strips contribute their actual
area; a centre outside a person never makes the surrounding cell background.
Flat or spatially concentrated texture cannot validate a translation. Strict
uses narrower limits. ORB geometry thresholds are unchanged.

Only transient descriptors are kept: ORB keypoints/descriptors at a maximum
640-pixel edge and 128-square RGB/mask arrays. There is no disk cache, new
dependency, detection, model inference, API request, or original-photo read.
The context-hash index filters candidates; every member across two components
must pass the same direct evidence before their merge. Protected manual groups
are excluded and cancellation commits no partial memberships.

Local evaluation reproduced a frozen 1,000-photo / 731-group baseline exactly.
The final second-round variant produced 691 groups, preserving every original
component. All 36 newly merged components (77 previews) were visually reviewed
without an obvious subject/scene switch. Four selected real negative pairs
remain separate. Seven additional real negative pairs have valid contexts on
both sides and reject in both input directions, including same-set subject
changes and same-costume scene changes. All three diagnostic positive pairs
now join. Seven additional positive pairs
retain four pre-existing joins and three misses (weak background, face change,
or multiple detected faces). Same-clothing/different-person and fixed-companion
body-change cases also have independent synthetic pixel checks. This is a
bounded repair, not a dataset-wide
accuracy estimate. The old 529-group partition was unavailable and is not
treated as ground truth. Existing saved workspaces are not automatically
recomputed by installing the new build or performing an ordinary rescan.

## Scaling and limitations

Each image is decoded once per cached source signature, storing only about
13.5 KiB of grayscale descriptor arrays per cached image (one whole-frame and
two optional crop descriptors; no retained full raster). The file feature cache
holds at most 256 entries. Fixed-representative hashes (legacy loading) or all
pending hashes (fresh grouping) are indexed in
separate whole-frame and crop Hamming-distance BK-trees, at most three indexed
entries per representative. A pair's covariance/pixel products are evaluated
only after the hash, aspect and subject tests and a cheap SSIM upper bound.
The upper bound follows Cauchy-Schwarz, so it cannot reject a pair that could
meet the threshold, apart from bounded floating-point roundoff.

There is no all-pairs grayscale matrix and no truncation to adjacent photos or
a time window. An identical burst needs one representative and N−1 structural
comparisons. Tests check 2,500 diverse synthetic hashes and a 2,500-image
identical burst, plus exact BK-tree search results against brute force.

This is not a guarantee of subquadratic runtime for all possible inputs. Many
colliding hashes with mutually incompatible grayscale structure can still
require many pair comparisons. Complete cross-component checks for portrait
edges can also have quadratic cost. Grayscale/hash methods can miss
large crops, translations and rotations, or confuse color-only changes. The
64×64 descriptor and fixed thresholds need evaluation on the user's real
photography before any calibrated accuracy claims. Frozen benchmark inputs
were not changed or used for threshold tuning.

## Persistence and integration

`assign_groups(assets, preset, preserve_existing=True,
protected_group_ids=(), stats=None)` preserves existing positive group IDs;
unassigned photos can join existing automatic representatives. Pass saved
manual IDs in `protected_group_ids`: those groups retain membership and cannot
gain automatic additions. An explicit regroup can use
`preserve_existing=False`; protected groups remain intact.

`group_store.manual_group_ids(path, collection_key)` returns protected IDs.
`load_groups(..., allow_partial=True)` is opt-in and applies only to manual
stores: surviving saved photos keep their IDs; newly imported photos remain
unassigned for grouping with those manual IDs protected. Default loading
continues to require an exact photo set. Loading never silently upgrades an
old grouping algorithm or rewrites a store.

Store version 2 retains human-readable `photo_stems` and `groups` for existing
readers, but membership loading uses the existing relocation-safe primary
source IDs (`photo_ids`, `groups_by_id`). Valid v1 stores with unique stems
remain readable. Ambiguous duplicate-stem v1 stores and malformed overlapping
memberships fail safely without partially changing any asset. Automatic saves
over a manual store are a no-op returning the unchanged path, unless an
explicit action authorizes `overwrite_manual=True`. Writes are atomic.

Manual split/merge do not globally renumber unrelated groups. Group-editor
neighbors use displayed group order, and its rows coalesce noncontiguous
members of each workspace-wide group. Contact sheets coalesce these groups
before paginating and keep member numbers stable across real page splits.
Double-clicking a group detail opens the full-screen viewer for that group.
The editor displays separate cached local keep/reject/review recommendations
and tied keep counts; regrouping refreshes these labels without decoding,
assigning stars, replacing manual/AI decisions, or forcing a single winner. The legacy `renumber_groups()` utility
remains available for an explicit caller that actually wants renumbering.

## Verification boundaries

Tests cover joint hash/structure rejection; identical-image and EXIF-orientation
matching; cross-folder, cross-date and nonadjacent grouping; representative
chain prevention; shuffle determinism; unavailable visual safety; stable IDs;
manual protection and old stores; source identity relocation; partial loads;
independent SSIM formula checks; hash-index correctness; and scaling counts.
The preset cutoffs were not tuned on real-photo accuracy benchmarks. Tk UI
execution requires a display; model-level grouping/editor checks do not.

### Current development verification

- Consolidated headless run: 156 passed, 2 display-dependent tests deselected;
  JUnit report: `verification/redesign-grouping/headless.xml`.
- Synthetic 5,000-asset index-only timing (no source decoding): diverse random
  hashes 4.950 s, zero full structural comparisons; identical burst 0.428 s,
  4,999 structural comparisons and one representative. This measures filtering
  work, not end-to-end real-photo speed or grouping accuracy. Raw counts:
  `verification/redesign-grouping/synthetic-scaling.json`.
- A broader exploratory run reached 78 passed and one Tk initialization failure
  because no DISPLAY was available. This is not a GUI verification pass.

### 2026-10-14 crop robustness evaluation

On 14 existing public landscape originals and six controlled central crops
each, matching increased from 21/84 to 55/84; all fourteen 5% crops matched.
There were zero matches in 1,183 different-source proxy pairs before and after.
These are transformation/source-identity proxies, not human grouping accuracy.
A real 42-file import (14 originals plus 2.5%/5% JPEG crops) changed from 38
groups to 14, with no cross-source groups. Measured wall time was 3.64 to 4.02
seconds; descriptor extraction for 14 originals was .74 to .95 seconds. This
is a small, warm-machine observation, not a throughput guarantee. See
`evaluations/robustness-fix-20261014/` for reproducible scripts and raw evidence.
Large crops, asymmetric framing, color-only changes and adversarial lookalikes
remain outside this validation.

The crop-enabled collision regression also verifies bounded 64-candidate
batches: 256 deliberately colliding 512px images take .859 s with the final
batch implementation versus 5.137 s with scalar fallback and .178 s before
crop support. All remain separate. The fivefold comparison increase is an
explicit worst-case cost; batching prevents disproportionate scalar overhead.
