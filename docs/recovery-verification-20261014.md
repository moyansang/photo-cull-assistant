# 2026-10-14 candidate reconstruction and verification

## Provenance

The prior cloud working directory and Git object database were not available at the time of recovery; the cause has not been established. This repository starts a new, explicitly dated recovery history. It does not reproduce the earlier Git commit graph.

Recovery root commit: `7c99377ec720c837d91daeb47d90f43a70d3f5e3`.

Verified round2 source ZIP: 43,264,045 bytes, SHA256 `8ce1345a2f8f280baed5ffcb572ecf6e32b776ff6964fb7eaf1c90c8c81f5e6d`.
Verified Windows patch/evidence archive: 880,812 bytes, SHA256 `69a4e0f4b7730cd4102eec7d3222f6c38a2066e0e2db04bcf2a53e46c247647f`.
Applied exactly once: `round2-original-to-tested-exact.patch`, 149,963 bytes, SHA256 `2bd4161b87efb626e307ae27131629929c9611233b5d04f203d6e81ae55e60f5`.

All 268 non-model files match the actual Windows-tested candidate byte for byte. Four model files match the original archive unchanged. Thus all 272 recovered files are accounted for. Subsequent new verification/documentation changes are recorded separately.

SOURCE_MANIFEST.json, ROUND2_MANIFEST.json and DEVELOPMENT_MANIFEST.json describe their original historical snapshots. They are not manifests of this recovered final candidate. CLOUD_TESTING.md also preserves a previous build13r4 validation note; current evidence is summarized here.

## Recovered candidate tests

Windows final supplied JUnit: 1,030 collected, 1,028 passed, 0 failed/errors, 2 skipped (CR2/NEF sample files unavailable). Windows native NTFS ChangeTime fast path was tested outside the restricted runner: 10 calls, zero content reads; content fingerprint fallback was separately tested. Actual Tk windows and programmatic controls were tested, along with an isolated EXE and real HEIF/HEIC, JPG and RW2 decoding. This does not establish physical input, cross-monitor DPI or real Lightroom Catalog acceptance.

Fresh cloud run on the exact recovered candidate: 1,030 collected, 824 passed, 24 skipped, 142 failures, 40 setup errors. Of 182 failures/errors, 177 require the unavailable Tk DISPLAY and 5 encounter the existing Windows-only updater's st_file_attributes access under Linux. The updater source was unchanged in this recovery. Skips: 11 Tk, 11 unconfigured real-codec fixtures, 2 Windows-specific cases. These are reported as actual limitations, not changed to passing results.

Comparison with the previous 801-pass cloud run: 789 exact old passing node IDs pass again; 11 real-codec cases now skip because those original fixtures are absent; one atomic replacement test became two passing parameterized variants. No previously passing node became a failure/error. This comparison is not a claim of fresh native-codec or Windows testing on Linux.

## Missing historical material

The original cloud-only test source files test_native_memory_rgb_transfer.py (3 cases) and test_mixed_workspace_interruptions.py (6 cases) were not in the Windows handoff. Those nine historical cases have not been restored and are not counted here. The corresponding production RGB ownership and cancellation/rollback fixes are present. Any newly authored checks are separate new verification, not reconstructed historical evidence.

The user-supplied second-round verification ZIP and HEIF v3 evidence ZIP were inspected in full. Neither contains Git objects/bundles or the two missing test sources. Their historical reports remain useful evidence of earlier runs.

## Remaining acceptance gates

- Real Lightroom Classic integration in an independent catalog.
- Physical mouse and real cross-monitor DPI interaction.
- Real authorized CR2 and NEF sample verification.
- Matching redistributed codec binary versions to exact source, notices and obligations before release. Actual Windows codec loading does not close licensing review.
- Landscape rejection thresholds still need appropriately labeled development/held-out data; earlier PIQ/CERTH evaluations must not be reused as independent holdout claims.

No remote push, release, replacement of the user's installed application, or paid AI call was performed during this recovery.

## New verification authored after recovery

`tests/test_recovery_guarantees_20261014.py` adds seven new synthetic cases: two owned-RGB transfer paths with redundant copy/convert forbidden; cancellation at the incremental-group checkpoint; cancellation during the final source validation in scan and sheet modes; and late publication rollback with an existing or previously absent source-location marker. The transaction tests compare prior mixed-workspace records and original bytes and then resume to successful completion. A focused run passed all seven in 0.40 seconds. These are new tests, not recovery of the lost nine cases. They do not reproduce historical 24-megapixel native peak-RSS measurements.

The new transaction checks establish cooperative cancellation and rollback after caught exceptions. They do not establish arbitrary process termination/power-loss recovery or cancellation inside publication itself. The supplied Windows full-suite log retains 65 Tk after-command diagnostics after window destruction; zero pytest failures must not be described as zero diagnostics.

Final combined cloud run after adding the seven new cases: 1,037 collected, 831 passed, 24 skipped, 142 failures and 40 setup errors, in 62.13 seconds. The complete failed/error node-ID and message mapping is identical to the 1,030-case recovered baseline above. No additional failure or error was introduced. The seven focused results are included here, not added again to the total.


## Later cloud readiness audit and process-crash recovery

On `52bd1b4` (including Tk repair `8a29559`), the 831 previously passing headless nodes plus ten Tcl lifecycle cases were selected explicitly and all 841 passed. This is distinct from the older 1037-case full-suite report above. Real RAW samples were subsequently restored and bounded native-memory measurements completed; see cloud-readiness-recovery-20261014.md.

Controlled process exits exposed a publication-recovery defect not covered by the seven exception/cancellation tests: moved artifacts could be mistaken for missing staging, causing backup deletion or a dangling incremental preview path. A transaction-journal repair is now under independent review with true subprocess-exit regressions. The historical nine missing test sources remain missing; the new tests do not claim to reconstruct them.

Final reviewed-candidate headless selection: **873 passed in 55.90 seconds**, comprising the 841 selected baseline cases and 32 new crash-recovery, lock, and stale-object cases. All production changes are confined to processing publication/recovery and the optional session JSON writer; no UI layout, image ranking, threshold or Windows installation change. This final selection does not include native Windows/Tk platform verification. Source hashes and raw output are retained in readiness-audit/final-candidate-manifest.json and headless-reviewed-final.log/.xml.
