# Viewer acceptance, 2026-10-13

The implementation and controller tests are complete. Rendered-platform acceptance
is blocked by the cloud executor's unavailable Tk display. Do not describe this as
a tested Windows build or visually verified UI.

| Requirement | Status | Evidence / remaining limit |
| --- | --- | --- |
| Group thumbnail double-click opens the selected photo | Complete, headless integration | Runs real GroupEditor thumbnail-binding loop, invokes the second photo's callback and verifies exact group order and index. |
| Review list, preview and Full-screen button entrypoints | Complete, wired; native event delivery blocked | All three bind to the real ReviewDialog opener. Tests run the opener with its real asset resolver, filtered order, repeated-open replacement and missing-source protection. |
| Mouse-centered zoom, drag pan, Fit and 100% | Complete, controller/pixel tests | Real handlers and PIL renderer exercised. Native scale snaps to integer source pixels, preventing half-pixel interpolation; odd-sized stripe pixels and oriented JPG pixels are compared exactly. Physical mouse/DPI behavior remains untested. |
| Preview then full-resolution JPG | Complete, async/controller integration | Worker is held after publishing preview; 100% is requested; EXIF-oriented original loads, stays native scale, and its actual decoded pixels match the displayed PIL crop. |
| RAW embedded/full source distinction and explicit upgrade | Complete, protocol/controller tests | Real request/poll/render flow retains JPG until explicit RAW response, does not downgrade to cached preview, and labels the returned source. Actual cameras/compression variants remain a separate decoder validation gate. |
| Left/right preserves viewing position | Complete, controller integration | Different source resolutions retain normalized center and fit-relative zoom. Edge clamping is intentional. After 100%, navigation preserves normalized field of view, so a differently sized next source can have a different pixel-scale percentage. |
| Full-screen toggle and Esc | Complete, controller; platform blocked | Real handlers request native full-screen; first Esc leaves full-screen and next closes. Window-manager behavior needs a real display. |
| JPG/PNG/BMP/TIFF/WebP switching | Complete, actual decoders/controller | Real files pass through the shared decoder and real viewer controller in sequence; format labels, original dimensions, prior-frame release and two-entry cache limits are checked. HEIF/AVIF and camera RAW coverage remain separately reported. |
| Bounded browsing cache and stale-result suppression | Complete, async tests | Byte/entry limits, copy ownership, replaced pending requests, late results, cache reuse and close-during-decode checked. Cache budget excludes active source/native-decoder working memory. |
| Browsing does not rescore, call paid AI or modify originals | Complete, tested | Viewer imports only read-only display decoder; controller tests fail on a paid-model call, verify unchanged original bytes and unchanged score/AI/reject fields. |
| Owned-window close/reopen and style preservation | Partial: code/tests prepared, platform blocked | Embedded owner resolves to native top-level. Existing theme/colors reused. Four real-Tk tests cover rendering, teardown/grab restoration and frame ownership but cannot run without DISPLAY. No screenshots were produced. |

## Limits that must remain visible

- Legacy cached previews have no reliable source metadata and are labeled as such
  until actual decoding supplies the source kind.
- Normalized view is preserved, not semantic subject tracking. If an old legacy
  preview and its original have different rotations, an upgrade can change the
  image coordinate frame; the viewer does not infer a face/body correspondence.
- Raster and RAW formats use the shared decoder. Passing synthetic backend or
  format-routing tests is not proof of support for every camera/compression file.
- No Windows full-screen, high-DPI, Chinese-font layout, physical input, or actual
  rendered Tk acceptance has been completed in this cloud environment.
