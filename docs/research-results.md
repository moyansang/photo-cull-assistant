# Results and AI lifecycle redesign research

Checked 2026-10-13. No external implementation was copied, no new model was downloaded, and no paid API request was made during implementation or testing.

## Reuse decisions and primary sources

- Keep the existing Pillow-based full-frame `ImageOps.contain` contact-sheet rendering and native PNG detail crops. The upstream [Pillow license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) identifies MIT-CMU terms and requires retaining copyright/permission notices. Existing dependency notices remain applicable; this work adds no vendored Pillow code.
- Reuse the application's bundled OpenCV Zoo/MediaPipe body candidate path instead of introducing a new heavyweight detector for the AI stage. The upstream [OpenCV Zoo license](https://github.com/opencv/opencv_zoo/blob/main/LICENSE) is Apache-2.0; the [pose estimation model directory](https://github.com/opencv/opencv_zoo/tree/main/models/pose_estimation_mediapipe) is the primary model reference. Model-specific distribution notices must continue to accompany the already-bundled assets.
- Considered Ultralytics for person detection, but did not introduce it. The current upstream [Ultralytics license](https://github.com/ultralytics/ultralytics/blob/main/LICENSE) is AGPL-3.0, with different distribution requirements from the existing dependency set. A detector replacement is unnecessary for the decision/invalidation work and would require a separate packaging/license review.
- Considered [Exif sample images](https://github.com/ianare/exif-samples) as future decoding fixtures. They were not downloaded or redistributed here; representative camera/codec coverage and individual fixture rights must be verified before including real photographic samples. Synthetic tests do not establish real-world culling accuracy.

## Implemented dependency contract

- `technical_fingerprint(asset, crops)` hashes source identities/stats, subject/crop settings, detection threshold, and manual analysis mode. It excludes group membership and computed group scores.
- Selection identity hashes all group members and their local/valid-AI technical evidence. A source/subject change invalidates technical review for that photo and selection for its group. Group moves invalidate old/new group selection but preserve technical AI. No invalidation sends an API request.
- Stale `ai` and `ai_focus_result` remain inspectable in persisted state. A later replacement archives previous technical results in `focus_history` and selection answers in `history`. Task regeneration preserves manual decisions and prior immutable task folders.
- One resolver supplies UI, filters, and Lightroom output: confirmed manual decision, then current AI selection, then current AI technical review, then local recommendation. Manual unspecified fields are omitted. Local recommendations never create a star value. Missing stars are omitted so the existing Lightroom plugin preserves catalog stars.
- Valid AI selection can run directly with local evidence; no technical-AI purchase is required. Web selection no longer silently bundles technical review. Existing web snapshots remain readable.

## Technical AI scope

Technical review returns clarity/evidence only; strict response validation rejects star fields. Portrait review prefers face/eye evidence, then explicitly selected or reliable detected bodies. Body evidence is labeled separately and cannot prove eye detail. Landscape mode supplies a full overview and distributed native windows without requiring portrait detection or boxes. Low-texture sky/water and ordinary depth-of-field blur are not direct rejection criteria.

Multiple subjects are reviewed separately. Problems remain attached to each subject; any non-clear participant leads to whole-photo review rather than automatic whole-photo rejection. Local evidence remains available separately from AI conclusions. Selection prompts compare full-frame expression/composition, permit ties, include available technical evidence, and do not force a quota.

## Validation boundaries

Regression tests cover source precedence, star omission, original/XMP preservation, regroup/crop/mode invalidation, legacy read paths, history persistence, API response validation, source cache identity, independent selection, and portrait/body/landscape evidence preparation. Model responses are mocked; tests exercise behavior and request content, not actual model quality. Live Tk visual QA remains unavailable in this cloud environment because no usable display server is attached. No desktop installation, packaging, publishing, or source deletion was performed.

## Wired acceptance (2026-10-13)

`tests/test_results_wired_acceptance.py` exercises the real headless pipeline with isolated synthetic PNG fixtures:

1. `start_job(..., mode='scan').run(...)` performs decoding, local analysis, grouping, sheet rendering and checkpointing. A configured model causes zero HTTP requests.
2. `ReviewProject.create_task`, `prompt` and `batch_images` produce a scoped selection request. `ai_api.call_model` constructs the actual multimodal HTTP payload; only `_open_request` is replaced with an in-memory simulated response. The test decodes each transmitted base64 JPEG, compares it byte-for-byte with the generated snapshot, and checks that all four distinct source-corner markers survive. This detects accidental subject-only cropping. The manifest contains real local technical evidence; no technical-AI result or forced technical supplement is required.
3. Strict response ingestion, a manual override and Lightroom export share the expected source priority. An unreviewed third photo receives the local fallback with no rating field.
4. Explicit technical review uses actual native-image preparation, caching and parsing with the same simulated transport. A changed subject box then passes through the real `rescan` processing stage, expires only affected technical/group suggestions, retains historical results, and sends no automatic request.
5. After explicit re-review, splitting the group preserves valid single-photo technical AI while expiring both affected group selection results. Manual stars survive. Earlier selection and technical records remain in history. Original PNG and XMP bytes remain unchanged.

The UI summary/filter methods also have no-Tk tests proving stale technical results are excluded and a manual keep is not counted as an AI rejection. Body-only selection suppresses unrelated automatic face detection. AI body result identity uses normalized `body_box` plus a stable `subject_id`; face/body association metadata is carried when available, and separate evidence targets are not counted as separate people.

Implemented and directly verified: the headless method flow above, actual generated image payloads, output semantics and invalidation persistence. Partial: photographic/model accuracy is still a calibration task; this test's provider responses are deterministic fixtures. Platform-blocked: live Tk interactions and visual desktop QA, Windows packaged execution, and actual Lightroom-host plugin application. Packaging and publication remain paused for review.
