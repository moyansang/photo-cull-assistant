# Local integration of cloud snapshot f65a100

This branch starts from local `8a70f58e450499e869eb9c4976c49e683548a25e`.
Cloud source is `f65a100b780bbb6629f85ae99ddc9495063f736a` (41 commits).
The histories have no Git common ancestor: cloud root `bb116f3bd0db55fa4d97195f61760c325245fc75`
is a recovered source snapshot, normalized by `1ad69cb397584865cc0f2a340b4d4c799f18463e`.
Changes are ported by feature with Cloud-source commit messages, without merging
unrelated histories. All 129 local-only paths are retained. 102 newline-only
changes are excluded. The 31 changed shared paths and 78 cloud-only paths are
an inventory, not a count of features.

## Adopted features and provenance

| Feature | Cloud source |
| --- | --- |
| Recorded versus submillisecond timing display | bb116f3 / 1ad69cb |
| Candidate provenance, supported face/body evidence, part-specific reasons | bb116f3 / 1ad69cb |
| Read-only group evidence and exact-photo person-editor navigation | bb116f3 / 1ad69cb / 68fac95 |
| Explicit manual body scope and stale measurement protection | 2785dc8 / 342f065 / fe8915e |
| Manual person uniqueness and scope-contract migration | 20c3ac9 |
| Optional multi-camera AF metadata CLI | 76b1a77 through ccc71ab, bounded to reader/parser files |
| Opt-in JPEG/RAW AF reference display | fda398b through f65a100, bounded to display files |

Windows adaptations preserve path-to-handle file identity while allowing the
platform's path/handle creation-time reporting difference. Same-API before/after
identity and SHA-256 checks remain required. Small-window AF controls share the
existing group header to retain the photo grid's minimum height.

## Explicitly deferred

- Round 4 body sampling v3, face sampling v7, expanded-source sampling, and their
  research entrypoints. Retained body v2 has the local v1 numeric sampling kernels
  and thresholds, plus ownership/source checks. `body_sampling.py` is absent.
- AF automatic person association, including Canon multi-person selection and
  AF-derived RAW quality/AI crops. `af_subject.py` is absent. No AF hook is added
  to local scanning, primary selection, AI cache, participant review or export.
- ADNet/FaceOcc, aggregation and Round 25–28 candidates. Research stays outside
  the production tree and portable package; no experiment or threshold search runs.

Manual scope takes priority, ambiguous/multiple automatic candidates still
require review, and automatic single-person selections remain
`human_confirmed=false`. Old measurements cannot be silently re-signed.
AF reference overlays have no person-selection or keep/discard authority.

## Runtime and acceptance boundary

Build 18 is an independent Windows acceptance candidate, not a released update.
It does not replace the installed/current portable program. No push is performed.
The AF checkbox is off by default and metadata access starts only after explicit
activation. It requires a separately installed ExifTool discoverable on PATH;
ExifTool is not bundled or downloaded. The CLI supports an explicit `--exiftool`
path. Missing tools remain a controlled unavailable state.

Synthetic regressions cover sampling parity, manual precedence, no automatic AF
selection, source changes, wrong pixels, stale background results and small-window
controls. They do not validate real-camera calibration or photographer intent.
Windows symbolic-link and open-file replacement scenarios are skipped only when
the OS actually denies constructing the required fixture; other mutation tests run.
Real-photo AF inspection, independent quality accuracy, paid API use and real
Lightroom acceptance are outside this local verification.

Cloud validation documents describe their original runs. Their numbers are not
this branch's Windows results. Local test/build counts and source hashes are kept
with the independent acceptance delivery.
