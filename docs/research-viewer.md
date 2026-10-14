# Photo inspection viewer: research and implementation

Research checked 2026-10-13. This is a new Tk implementation preserving the
application's existing workbench theme. No third-party viewer source was copied.

## Reuse assessment

- [Cullumi upstream](https://github.com/Yuumi0221/Cullumi): reference for the
  user's requested inspect-and-browse interaction. Its README describes enlarged
  viewing, previous/next keyboard navigation and cached previews. The visible
  repository root did not expose a license. Treat this as behavior research,
  not permission to copy code. Its WebView frontend is also a different UI stack.
- [PyQtImageViewer upstream](https://github.com/marcel-goldschen-ohm/PyQtImageViewer):
  provides reusable zoom/pan and image-stack widgets, with MIT licensing stated
  in its README. Adopting it would introduce a Qt UI stack beside the existing
  Tk application. The viewer therefore retains Tk; no code or dependency was
  imported from this project.
- Existing Pillow is retained for display transforms. Its upstream
  [license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) is MIT-CMU.
  The [Image.transform API](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.transform)
  permits viewport-sized affine output, avoiding enormous intermediate images
  at high zoom. Application-specific viewport and worker logic is original.

## Implemented contract

`ImageViewer(parent, assets, index=0, fullscreen=True)` opens a read-only viewer.
An optional `on_index_changed(index)` is for selection only; callers must not
connect it to scoring or a paid request. The implemented AI review entrypoint
opens the current filtered list; the former OS-viewer action remains in the
preview context menu.

Controls: double-click or F11 toggles full-screen; Esc leaves full-screen, then
closes a windowed viewer; wheel zooms about the pointer; drag pans; Fit / 0 fits
the picture; 100% / 1 displays source pixels; left/right browses. Navigation
keeps normalized center and fit-relative magnification. Differing aspect
ratios retain that normalized center subject to picture-edge clamping. An
in-place preview-to-original upgrade preserves normalized view, except when
100% was explicitly requested, which remains native-pixel scale.

An existing cached preview is published first. The worker then calls the shared
`image_decode.load_display_image` API for the preferred full-size JPG/standard
source, or RAW embedded preview with shared decoder fallback. Full RAW inspection
is explicit via the RAW button. Source names distinguish JPG, standard format,
RAW embedded JPG/bitmap, and RAW full decode. A legacy workspace preview's source
is unknown, so it is honestly labeled as a cached preview until actual source
loading completes. Source errors remain visible and are never called poor quality.

The viewer writes no original, score, rating, rejection, group, subject, or AI
state. Its own cache is memory-only and separate from scoring. Up to three
entries and 192 MiB estimated decoded pixel storage are retained. Each RGB image
is charged at least four bytes per pixel. Oversized images can be inspected but
are not cached. The active image, one in-flight decoder and viewport-sized Tk
buffer are additional live allocations, so this is a cache budget, not a claim
of a hard process-RSS limit. The shared decoder has its own limits.

One daemon worker permits one in-flight decode and one replaceable pending
request. New navigation discards queued older results. A running native decoder
cannot be forcibly interrupted safely; its result is closed and never displayed
if its generation is stale. Closing stops new work, clears queued frames and
releases the cache once the current decode finishes. All Tk work stays on the
UI thread.

## Verification and limitations

- `tests/test_image_viewer.py`: pure viewport, cache, source identity, decoder
  integration, preview-first sequencing, stale generation, repeated browsing,
  close-during-decode and AI-review entrypoint tests.
- `tests/test_image_viewer_ui.py`: real Tk rendering, controls, navigation,
  full-screen/Esc and owner-window teardown tests. These require a live display.
- On this cloud executor, Tk tests were attempted but failed during root-window
  setup because DISPLAY is unset. Xorg with its installed dummy driver could
  not create local listening sockets; the escalation attempt returned
  `CreateProcess: TurnAborted`. No rendered Tk screenshots were produced.
- No Windows/DPI/full-screen window-manager behavior, physical mouse input, or
  representative camera corpus has been tested here. These remain validation
  gates before a Windows release.

Final acceptance details are in [viewer-acceptance.md](viewer-acceptance.md).
`tests/test_viewer_controller_integration.py` additionally executes the real
entrypoint/controller methods with only Tk surfaces substituted, including
pixel-exact 100% rendering and actual JPG/PNG/BMP/TIFF/WebP switching. Native
scale now snaps its source origin to whole pixels, avoiding the half-pixel blur
that can otherwise occur with odd source dimensions or fractional panning.
