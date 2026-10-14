# Conservative landscape robustness candidate, 2026-10-14

Status: implementation under validation; no release or generalized accuracy claim.
Baseline: `70f73a99b1902f37a135701898a462feac68df80`.

## Crop alignment result

The original whole-frame matcher is unchanged. A failed match may additionally
compare a 2.5% or 5% central trim to the other whole frame, never trim-to-trim.
Original aspect/subject gates remain; aligned dHash <= 8 and block SSIM >= .86
(or stricter configured thresholds). Both directions use the same fixed group
representative, preserving no-chain behavior and manual group protection.
Two descriptors are derived from a bounded 512-pixel grayscale snapshot without
additional source reads. No file deletion, ratings, AI requests or time gating.

Using the existing five KonIQ landscape/outdoor selections and nine SPAQ
Landscape-labelled photos (see earlier reports for licensing, source checksums,
selection and human quality-label limitations):

| Proxy test | Original | Candidate |
| --- | ---: | ---: |
| Same-source central crop pairs, 84 | 21 match | 55 match |
| 1% crop, 14 | 13 | 13 |
| 2% crop, 14 | 7 | 13 |
| 3% crop, 14 | 1 | 14 |
| 5% crop, 14 | 0 | 14 |
| 7% crop, 14 | 0 | 1 |
| 10% crop, 14 | 0 | 0 |
| Different-source proxy pairs, 1,183 | 0 match | 0 match |
| 42 JPEG files: originals + 2.5%/5% crops | 38 groups | 14 groups |
| Cross-source groups in the 42-file run | 0 | 0 |
| 42-file end-to-end seconds | 3.639 | 4.020 |
| 14 original descriptor extractions, seconds | .736 | .945 |

The negative proxy pool comprises 91 original-original pairs and 1,092
original/other-source-crop pairs. Source identity and controlled transforms are
not manual similarity-group labels. Zero observed false matches is not a
population false-match estimate. Larger/asymmetric crops, rotations, dense
near-lookalikes and color-only differences remain unvalidated. This bounded
fallback deliberately does not recover all the positives of the earlier broad
four-scale experiment. No preset cutoff was relaxed.

Feature arrays rise from about 4.5 to at most 13.5 KiB per cached image; cache
capacity remains 256, with at most three representative index entries. Real
extraction time rose about 28% and small end-to-end time about 10%; these are
single small-sample timings, not a throughput guarantee. Collision-heavy
worst cases can still approach quadratic representative comparisons.

Reproduce from the repository root:

```sh
PYTHONPATH=src /workspace/scratch/d4e33c625c08/cloud_qa_homepage/.venv/bin/python \
  evaluations/robustness-fix-20261014/evaluate_grouping.py \
  --koniq-data /workspace/scratch/d4e33c625c08/landscape-evaluation-data \
  --spaq-data /workspace/scratch/d4e33c625c08/spaq-evaluation-data \
  --output evaluations/robustness-fix-20261014/results
```

Raw pair-level evidence, source hashes, algorithm hash, timings and production
assignment groups are in `results/grouping-comparison.json`. Original hashes
were verified unchanged after execution. The script loads baseline grouping
from Git into a separate temporary package module, and evaluates production
functions, not a surrogate matching formula.

## Regression and remaining validation

Focused grouping run before batch hardening: 79 passed; the final crop module
contains 46 passing cases. New tests include both orientations,
all presets, input shuffle, no chaining, incremental/manual protection,
whole-frame gates, stricter fallback, no trim-to-trim matches, bounded immutable
variants, tiny inputs, and twelve unrelated structured synthetic sources.
Independent review also found that the old 64px collision fixtures did not
exercise crop descriptors. A 512px version exposed a 24x scalar-path slowdown.
The crop fallback now batches at most 64 representatives, preserving candidate
ordering and scalar boundary rechecks. Added tests prove scalar/batch equality
across candidate-rank boundaries, missing crop banks, both alignment directions,
SSIM decision boundaries, and the stricter aligned portrait-body hash gate.
The new crop-enabled collision fixture asserts identical membership and
bounded scratch arrays. Final uncontended 256-image timings: baseline .1775 s,
scalar crop 5.1365 s, batched crop .8585 s. Scalar and batch both performed
163,200 comparisons versus baseline 32,640; all 256 remained separate. Batching
removes the disproportionate scalar overhead (~6x faster), but the extra four
alignment comparisons still cost ~4.84x baseline in this adversarial fixture.
At 64/128 images the batched times were .0773/.2251 s against .0194/.0563 s
baseline. See `evaluate_crop_collisions.py`, `crop-collisions.log` and
`results/crop-collisions.json`.

The aggregate runner preserves the exact 873 node IDs from the previously
reviewed headless JUnit selection and adds both new robustness test modules.
Run `python evaluations/robustness-fix-20261014/run_headless.py` in the project
venv. GUI/Tk and Windows HEIF verification are outside this headless selection.

Final aggregate: **983 passed in 80.29 seconds**, zero skipped/failures/errors.
JUnit membership was checked to retain all 873 original case IDs, plus 46 crop
and 64 noise cases. Source/test hashes are in `headless-manifest.json`.
## Landscape noise-support result

The existing texture/focus thresholds and raw region metrics are unchanged.
Only already-clear tiles receive a support check. A unit-energy second-difference
filter estimates grayscale noise sigma via median absolute residual; expected
noise power through the existing Gaussian/Laplacian filter is compared with
reconstructed measured fine-detail power. A tile is supported only when estimated
signal power exceeds noise power (noise fraction < .5). At least two supported
clear tiles are still required. An unsupported tile can only remove keep support;
there is no new reject path, no local stars, no face/box requirement, and no
manual/AI priority change. Landscape-only algorithm metadata records the change.

Offline replay of existing inputs:
- 14 real originals: all decisions unchanged (13 keep, 1 review).
- 25 existing transformed JPEGs: all unchanged (20 keep, 5 review).
- Two known problem inputs × six independent Gaussian-noise levels × five seeds:
  60 controlled cases, baseline 45 keep/15 review, candidate 0 keep/60 review.
  These are artificial noise interventions on real images, not 60 human labels.
- 36 sparse natural-texture interventions: 36 keep becomes 30 keep/6 review.
  All six downgrades are noisy rock texture at sigma 8/16. This is a measurable
  conservative cost, not proof those photos deserve discard.
- An additional nine sparse water/reflection interventions preserve the three
  unnoised repeats but downgrade all six sigma8/16 repeats. Together with the
  rock examples this is twelve observed noisy-texture downgrades.
- Correlated-noise examples: all 3 still keep. JPEG-compressed noise: 6 of 9
  still keep. 51 of 213 controlled synthetic threshold-adjacent promotions also
  remain. This gate is **not complete noise invariance**.

Isolated five-repeat timing (sum of per-image medians for 14 originals):
1.304 s baseline to 1.497 s candidate, about +14.8%. This is analysis only,
not source decoding or a throughput guarantee; see the noise runtime JSON.

Raw tile evidence, source hashes and reproducible scripts are in
`../noise-fix-20261014/`. All original-region metrics and source bytes were checked
unchanged. The 64 noise unit cases cover real filter gain, denominator recovery,
RGB/grayscale noise, true sparse checkerboard detail, tiny/nonfinite inputs,
source immutability, bounded tiles and landscape-only versioning. Independent
review additionally checked manual/AI priority and estimated the real filter
gain by a separate white-noise experiment (within .3% of the impulse constant).

Low human SPAQ Sharpness is not an automatic
keep/reject ground truth; the candidate must not introduce automatic discard.

Independent read-only reviews accepted the final grouping and noise patches
with no remaining code blockers. Noise review ran 85 relevant tests independently
and confirmed both priority and gain behavior. No commit or push was performed
by this implementation task.
