# Manual person quality scope

This cloud change is based on fe8915e. It changes scope isolation, not quality thresholds or grouping algorithms.

## Selection and measurement

- Select the desired face(s) in the existing subject editor and save the explicit confirmation. Unselected background faces do not contribute to their quality decision.
- A manual face rectangle containing multiple detected faces no longer chooses the nearest or highest-score face. Its landmarks remain unknown; the user must reduce or separate the rectangles. AI review also returns unavailable before submitting that ambiguous rectangle.
- Exactly one detected face remains supported; no detection is not semantic occlusion proof and remains governed by existing insufficient-evidence rules.
- Face/body selection records share validation, hidden/disabled semantics and exact-duplicate removal. Measurement retains first occurrence order; confirmation uses stable sorted identities. Malformed collections are not partially accepted.
- Explicit body selections are authoritative. Only body records matching selected boxes may join selected faces. Body/head correspondence still requires unique existing evidence; a hand-drawn rectangle does not prove instance ownership.

## Optional body editing without changing face-first display

The subject page continues to show face boxes when faces are present, and body fallback otherwise. The existing body-dialog entry opens face-first, read-only by default. When the user has explicitly selected faces, the optional body-scope switch permits body editing in that dialog only. Automatic face candidates are not silently promoted: first select the intended face(s). Opening, toggling, or cancelling does not save or confirm anything. Apply changes only the draft; original explicit save confirmation and local rescan/regroup remain necessary.

No selected body plus Apply means explicit [] (body inspection disabled); Restore automatic means the key is absent (existing optional automatic behavior). These are intentionally different. Main face crops, AF policy and other page layouts are unchanged.

## Evidence migration

Manual-face measurements and technical-review scopes carry `unique-contained-manual-face-v1`. Old local/AI verdicts without this contract are not reused as current decisions. User confirmation is preserved. Saving the same confirmed scope or using local rescan schedules remeasurement of stale contracts; users need not move a correct box merely to invalidate a cache. A new AI request can still be built for a valid confirmed scope with no local measurements, but every face is checked for detected ambiguity before any AI cache/upload.

Automatic-only and body-only scope contracts retain their prior behavior. No API is called by opening, saving, or locally rescanning this editor. Manual ratings/picks and manual groups retain priority through regroup/export.

## Validation boundary

Deterministic tests control detector/metric outputs to prove scope, identity, cache and UI callback behavior; they are not natural-photo accuracy measurements. Previously revealed fixed research face rectangles are checked without new annotation or threshold search. Real GUI/native Windows layout acceptance remains unverified in the headless environment. This repair does not establish reliable AF-selected people, semantic eye visibility, or improved blur classification.
