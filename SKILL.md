---
name: tabletop-shadow
description: Add offline tabletop shadows to a single tobacco pipe on a plain background. Anchor every camera angle at the complete object's lowest visible point, with protected product pixels and batch processing. Use for pipe product photos, contact shadows, grounding, 烟斗阴影 and 竖立烟斗底部阴影; not background replacement or generative retouching.
license: MIT
metadata:
  hermes:
    tags: [image-processing, pipes, shadows, offline]
---

# Tabletop Shadow

Use the bundled Python renderer through `terminal`. It needs Python 3.10+, Pillow and NumPy. Processing is local, with no image-generation service, model download, API key or GPU.

Preserve the original pipe, wood grain, gloss, exposure, position, orientation, dimensions and background. The renderer modifies the background and verifies that the segmented opaque product protection region is unchanged. Inspect that mask as well as the resulting image; this is an empirical 2D approximation, not a reconstructed 3D scene.

## Run

Check that the interpreter used by `terminal` can import `PIL` and `numpy`. If dependencies are missing, report that or follow the user's setup instructions; do not silently download dependencies during an offline task.

```sh
python "${HERMES_SKILL_DIR}/scripts/add_shadow.py" "input.png" --output "shadow-output" --debug
```

An input directory processes a batch; add `--recursive` for subdirectories. Every image is processed independently. Output is a lossless `*_shadow.png`, a transparent `*_shadow-only.png` layer, a `*_shadow-report.json`, and an optional `*_protected-mask.png`. For opaque input the overlay has zero alpha in the protected product region, so it can be composited over the original. Source images are never overwritten; use a new output directory for revisions.

Batch preflight rejects filename/output collisions and aliases to supplied inputs. Do not use `--overwrite` to bypass that error. New generated PNGs and outputs identified by verification reports are skipped in directory batches; select original-input folders for old unmarked/reportless images. Unknown configuration keys and malformed values are errors, not ignored settings. A nonzero exit or any per-image `ok: false` means that image did not complete; an old report must not be substituted for this run's success.

For a confidently uniform opaque background, faint neutral old-shadow residue near the support is cleaned first. When this happens, `*_cleaned-base.png` is also saved; composite the shadow over that base rather than the uncorrected original. `--keep-existing-shadows` disables this cleanup. The complete protection mask and an outer two-pixel guard are not cleaned.

Hermes expands `${HERMES_SKILL_DIR}`. If template expansion is disabled, use the actual skill directory provided in the activation message. On systems using `python3`, substitute that executable. Use an interpreter with the dependencies installed, rather than an unrelated code-execution sandbox.

## Apply the tabletop rule

Treat every input as resting on a tabletop through the lowest point of its complete visible silhouette. Keep this convention even when the camera angle makes that placement physically implausible. Include the mouthpiece if it extends below the wooden body. Normally let the renderer infer this coordinate; do not move the pipe down to the canvas edge.

A visible bowl opening or an overhead-looking angle does not change this rule. The bundled `bowl-facing.png` uses `side`. Choose only the footprint shape below; do not ask the user to decide whether each photo is physically flat or standing.

| Mode | Use |
|---|---|
| `--view side` | A broad footprint at the lowest foreground y; raised parts retain a visible diffuse cast shadow. |
| `--view upright` | A compact footprint around the lowest support, suited to vertical compositions; upper parts do not receive a halo. |
| `--view auto` | Tall foregrounds use upright, other foregrounds use side; both use the same tabletop baseline. |

Since v1.3.0, legacy `--view flat` is a compatibility alias for grounded `auto` and produces a report warning. Prefer `auto`, `side` or `upright` in new commands and configuration. No mode creates an all-around flat-lay silhouette shadow.

## Adjust and verify

Start with defaults. `--strength` changes overall opacity; `--softness` changes spread; `--stem-strength` controls the mouthpiece cast shadow in side mode and the faint distant base cast in upright mode. `--ground-y` overrides the support-plane y coordinate when needed. For subjective adjustments with an unclear direction, show two or three variants changing one parameter at a time.

Keep a denser contact region, softly fading surrounding shadow, and a visible but more diffuse shadow for elevated parts in a side view. Avoid a uniform long gray band, a thin outline, a floating gap, or a halo beside an upright pipe's whole height.

Before delivery:

- Inspect the composite and protection mask, including pale highlights and edges.
- Require `foreground_changed_pixels: 0` and `saved_png_verified: true` in each report. These checks describe the protection mask, not unverified semantic segmentation accuracy.
- Inspect crop warnings and the actual support point. A passing report is not an aesthetic approval.
- Body selection uses silhouette thickness first. A color-based split is accepted only if it agrees with the bulky-body coverage and actual support; a black bowl with a long brown shank must not be inverted. Inspect `body_detection` and `body_bbox_xyxy` in the report.
- Use `--mask` for a supplied accurate single-image foreground mask. Busy backgrounds, strong old shadows, multiple objects, low-contrast or reflective materials may require manual adaptation.
- Deliver PNG masters and useful before/after previews. JPEG recompression does not preserve pixel equality.

See [references/usage.md](references/usage.md) for configuration, transparency and limitations. Run `python "${HERMES_SKILL_DIR}/scripts/test_add_shadow.py"` for regression tests.

The validated real-image preset currently covers three different pipes, 18 photographs. Other small-object categories need their own visual validation; do not advertise universal support from these tests.
