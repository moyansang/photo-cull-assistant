# Experimental body-focus models

This optional module checks the selected person's visible torso and major arm
and leg segments after the separate face-focus decision. It is experimental:
the initial four user-labelled motion-blur examples all produce a conservative
`uncertain` result, but the current clear comparison set also contains many
`uncertain` results. It must therefore stay behind an explicit application
setting until a broader, body-specific labelled set is available.

Run `python body_models/download.py` from the repository root. The script puts
the exact pinned models in ignored `build/body_models`, verifies size and
SHA-256, and never downloads during photo processing. The application may set
`AI_CULL_BODY_MODEL_DIR` or package the same verified files under
`ai_cull_assistant/data/body_models`.

`assess_body_focus(PIL_image, normalized_face_box)` returns JSON-compatible
evidence. `state` is `clear`, `severe_blur`, or `uncertain`. `review_kind`
distinguishes an actual `motion_suspected` crop from an `unsupported` detector,
pose, or texture case. Only `motion_confirmed` is eligible for automatic body
rejection. `body_review_boxes()` selects at most three native-pixel evidence
boxes for a later API review.

Low texture, low contrast, or directional edges alone never produce
`severe_blur`. Hair, fingertips, clothing hems, and invisible body parts are
not rejection gates. A headshot without visible body returns `clear` with the
reason `body_not_visible_headshot`; this means the body check was skipped, not
that invisible limbs were judged sharp.
