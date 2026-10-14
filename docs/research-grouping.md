# Workspace-wide similarity grouping

Implemented 2026-10-13. Algorithm version: `workspace-dhash64-block-ssim-v1`.

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
- Every automatic member must pass against its group's fixed representative.
  A-B and B-C alone cannot cause C to join A's group. The representative
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

## Scaling and limitations

Each image is decoded once per cached source signature, storing only about
4.5 KiB of grayscale descriptor data per retained representative. The file
feature cache holds at most 256 entries. Only representative hashes are indexed
in a Hamming-distance BK-tree. A pair's covariance/pixel products are evaluated
only after the hash, aspect and subject tests and a cheap SSIM upper bound.
The upper bound follows Cauchy-Schwarz, so it cannot reject a pair that could
meet the threshold, apart from bounded floating-point roundoff.

There is no all-pairs grayscale matrix and no truncation to adjacent photos or
a time window. An identical burst needs one representative and N−1 structural
comparisons. Tests check 2,500 diverse synthetic hashes and a 2,500-image
identical burst, plus exact BK-tree search results against brute force.

This is not a guarantee of subquadratic runtime for all possible inputs. Many
colliding hashes with mutually incompatible grayscale structure can still
require many representative comparisons. Grayscale/hash methods can miss
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
