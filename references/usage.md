# Usage reference

## Placement

Every photo rests on a tabletop through the complete foreground's lowest visible point, regardless of camera angle or physical plausibility. This is a deliberate screen-space convention. The default plane y is the maximum foreground y, including a mouthpiece below the bowl; the original canvas and object position are preserved.

`side` gives a broad tabletop footprint; `upright` concentrates it around the lowest support. `auto` chooses a footprint shape from bounding-box proportions: height greater than 1.25 times width selects upright, otherwise side. A visible bowl opening does not change the tabletop rule. The bundled `bowl-facing.png` (formerly `top-view.png`) uses `side`.

From v1.3.0, `flat` remains accepted only as a compatibility alias for grounded `auto`, with a warning in the report. Old flat configs no longer produce an all-around silhouette shadow.

## Parameters

| Option | Default | Meaning |
|---|---|---|
| `--view` | `auto` | `auto`, `side` or `upright`; legacy `flat` aliases `auto`. |
| `--strength` | `1.0` | Overall shadow-opacity multiplier, allowed 0.1–2.0. |
| `--stem-strength` | `1.0` | Mouthpiece cast strength in side mode; distant base cast in upright mode, 0.1–2.5. |
| `--softness` | `1.0` | Spread multiplier, 0.4–2.0. Upright contact stays tight while the surrounding footprint changes. |
| `--ground-y` | foreground maximum y | Explicit support-plane override in displayed-image pixels; normally leave inferred. |
| `--method` | `calibrated` | Side renderer: measured soft footprint or alternative `projected` silhouette. |
| `--depth` | `0.18` | Side `projected` method only; vertical compression, 0.06–0.45. |
| `--light-x` | `0.06` | Side `projected` method only; horizontal projection shear, -0.3–0.3. |
| `--mask` | none | Single-image, white-foreground/black-background mask matching the oriented image dimensions. |
| `--debug` | off | Save the protected-product mask. |
| `--recursive` | off | Walk subdirectories, retaining their relative layout. |
| `--overwrite` | off | Replace previous generated outputs, never the source image. |
| `--keep-existing-shadows` | off | Disable cleaning faint neutral old-shadow residue near the support on a uniform opaque background. |

For visual tuning, compare e.g. strength 0.8, 1.0 and 1.15. Change one parameter at a time. A wrong placement mode is not an opacity problem.

## Per-image configuration

```json
{
  "defaults": {"strength": 1.0, "softness": 1.0},
  "files": {
    "side.png": {"view": "side"},
    "upright.png": {"view": "upright"},
    "bowl-facing.png": {"view": "side"}
  }
}
```

Keys are input-root-relative paths using `/`. Command-line arguments override per-image options, which override `defaults`. Replace old `flat` entries with `auto`, `side` or `upright`; existing flat entries remain usable through the grounded compatibility alias.

Only documented option names are accepted. RGB backgrounds must be three integers in 0–255 and `keep_existing_shadows` must be a JSON boolean. Invalid configuration is rejected before batch writes.

## Batch input and output separation

The output plan is checked before rendering: same-stem/different-extension collisions, or aliases to supplied images/masks/configuration, are errors even with `--overwrite`. Source/mask hardlinks are protected too.

New PNG artifacts include a `tabletop-shadow-artifact` text field containing generator, version and output role only, without input paths. Directory batches skip these known artifacts and legacy outputs named in valid sibling verification reports. Filenames containing `_shadow` are not excluded by themselves. Without a marker or report, an old result cannot be safely distinguished from an original; select source-only folders in that case. Single explicit image inputs remain intentional processing requests.

A nonzero exit or `ok: false` means the affected image failed. Do not treat a previously existing report as proof that a failed overwrite run succeeded.

## Transparency and masks

Transparent inputs use source alpha and retain a transparent canvas by default. A configuration entry such as `"background": [235, 231, 223]` flattens a transparent input onto that color. It does not replace an opaque image's existing background.

The protection mask comes from source alpha, a supplied mask or plain-background color difference. Low-contrast areas enclosed inside an opaque object are filled to protect pale wood highlights. A supplied overall foreground mask controls protection and shape; the wood/mouthpiece split is still a color heuristic. A mask alone does not provide 3D geometry or fix every unusual material.

Body selection first uses cross-section thickness along the silhouette's major screen direction. A material-based split is used only if it covers that bulky body and agrees with its support. This prevents dark bowls / brown shanks or dark wood grain from reversing or fragmenting the body identification.

For opaque inputs, `*_shadow-only.png` is a masked overlay with zero alpha in the protected region. If faint neutral old-shadow residue was cleaned, `*_cleaned-base.png` is provided: compose the overlay over that base to reproduce the composite. Otherwise compose over the original. Background cleanup is limited to the support vicinity, excludes a two-pixel guard around the product, and is disabled for transparent/user-masked input. Use `--keep-existing-shadows` to retain the original base exactly. For transparent inputs, put the shadow behind the pipe. The composite retains opaque product pixels; partially transparent edges may blend with a requested background.

## Reports

- `foreground_changed_pixels`: opaque protected-region RGB changes; must be 0.
- `saved_png_verified`: saved PNG equals the in-memory lossless result.
- `source_sha256`: fingerprint for checking the source was not modified.
- `view_used`, `method`, `geometry`: selected rendering behavior and support estimate.
- `placement_rule`, `foreground_lowest_y`, `ground_y_source`: lowest-point tabletop convention and whether the baseline was explicitly overridden.
- `warnings`: crop or shadow-near-canvas-edge findings.
- `body_detection`, `body_bbox_xyxy`: shape/material validation and selected body region.
- `old_background_shadow_pixels_cleaned`: background corrections; a cleaned-base output is saved when nonzero.

Reports include local input/output paths for diagnostics. Review them before sharing outside your own machine. Example-generation reports are intentionally not committed to this repository or included in releases.

`calibration_rmse` describes fitting error on the reference background only, not a quality score for a new photo. Inspect real contact, shadow spread, edge artifacts and the protection mask. JPEG recompression does not retain PNG pixel equality.
