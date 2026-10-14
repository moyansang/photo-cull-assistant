# Build 13 r4 verification

r4 reduces only short-homepage outer vertical padding to show a complete
Chinese log line. At 836x536, simulated 192 DPI: before content height 16px
and clipped dlineinfo/bbox height 16px; after content height 28px and complete
24px glyph line. Controls remain visible without homepage scrolling.

Regression tests now insert Chinese text and assert actual named-font line
metrics, content insets, dlineinfo and first/last-character bbox. Related App
tests use _close() so scheduled callbacks are cancelled before destruction.
The final 58-test targeted Windows run has zero invalid-command diagnostics.
The rebuilt isolated candidate EXE self-test passed. No installed program
was replaced, and no full local suite was run.

All tracked source, tests, necessary models/resources and licenses are present.
SOURCE_MANIFEST.json lists each file and SHA256. verification contains raw
targeted output, JUnit XML, before/after pixel evidence, geometry and EXE report.

Suggested Python 3.12 commands from the extracted source root:

    python -m pip install -e ".[raw,dev]"
    python body_models/download.py --verify-only
    python -X utf8 -m pytest tests/test_open_results_dir.py tests/test_workspace_layout.py -q -p no:cacheprovider
    python -X utf8 -m pytest tests -q -p no:cacheprovider --junitxml=cloud-results.xml

For GUI verification use a working display and appropriate Chinese fonts:

    python -X utf8 -m pytest tests/test_home_layout.py tests/test_page_chrome.py tests/test_home_results_integration.py -q -p no:cacheprovider

If Tk display sockets are blocked, retain the environment limitation. Do not
weaken the tests. Known prior cloud limitations: Tk display availability,
Windows path semantics, and existing source-relocation Linux failure. Verify
against the current package; do not label previous r2/r3 reports as r4 tests.
No deployment or installation is authorized by this package.
