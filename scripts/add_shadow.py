#!/usr/bin/env python3
"""Local, non-generative tabletop shadows for one pipe on a plain background.

Only Pillow and NumPy are used. No network, model weights, subprocesses or GPU.
Coordinates refer to the displayed image after applying EXIF orientation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
from pathlib import Path, PureWindowsPath

try:
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps, PngImagePlugin
except ImportError as exc:
    raise SystemExit(
        "Missing local dependency: Pillow / numpy. See this skill's requirements.txt. "
        "No dependencies or models will be downloaded automatically."
    ) from exc

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
VERSION = "1.3.0"
ARTIFACT_KEY = "tabletop-shadow-artifact"


def largest_component(binary):
    """8-connected components using row runs; no OpenCV / SciPy dependency."""
    parents, areas, runs = [], [], []

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    previous = []
    for y, row in enumerate(binary):
        edges = np.flatnonzero(np.diff(np.pad(row.astype(np.int8), (1, 1))))
        current = []
        j = 0
        for left, right in edges.reshape(-1, 2):
            i = len(parents)
            parents.append(i)
            areas.append(int(right - left))
            while j < len(previous) and previous[j][1] < left:
                j += 1
            k = j
            while k < len(previous) and previous[k][0] <= right:
                a, b = root(i), root(previous[k][2])
                if a != b:
                    parents[b] = a
                    areas[a] += areas[b]
                k += 1
            item = (int(left), int(right), i)
            current.append(item)
            runs.append((y, *item))
        previous = current
    if not parents:
        raise ValueError("No foreground found. Supply a white-foreground --mask if necessary.")
    totals = {}
    for i in range(len(parents)):
        r = root(i)
        totals[r] = areas[r]
    best = max(totals, key=totals.get)
    result = np.zeros_like(binary, dtype=bool)
    for y, left, right, i in runs:
        if root(i) == best:
            result[y, left:right] = True
    return result, totals[best] / max(1, int(binary.sum()))


def as_image(a):
    return Image.fromarray(np.uint8(np.clip(a, 0, 1) * 255 + 0.5))


def blur(a, radius):
    im = as_image(a)
    if radius > 0:
        im = im.filter(ImageFilter.GaussianBlur(float(radius)))
    return np.asarray(im, dtype=np.float32) / 255.0


def bbox(mask):
    ys, xs = np.where(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]


def estimate_background(rgb):
    h, w = rgb.shape[:2]
    n = max(2, int(min(h, w) * 0.018))
    frame = np.concatenate((rgb[:n].reshape(-1, 3), rgb[-n:].reshape(-1, 3),
                            rgb[:, :n].reshape(-1, 3), rgb[:, -n:].reshape(-1, 3)))
    quantized = (frame.astype(np.int32) // 12)
    codes = quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2]
    common = np.bincount(codes).argmax()
    cluster = frame[codes == common]
    background = np.median(cluster, axis=0)
    distances = np.linalg.norm(frame.astype(np.float32) - background, axis=1)
    matching = distances < 24
    if matching.mean() < 0.75:
        raise ValueError("Background is not sufficiently uniform at the border; use --mask.")
    noise = float(np.percentile(distances[matching], 98))
    return background, noise, float(matching.mean())


def shape_parts(foreground):
    """Find the thicker connected body without relying on wood / stem color.

    A dark bowl must not be mistaken for the mouthpiece simply because the
    shank is brown. Cross-section thickness is evaluated along the major screen
    direction; brief narrow waists between bulky lobes are bridged.
    """
    left, top, right, bottom = bbox(foreground)
    vertical = bottom-top > (right-left)*1.25
    canonical = foreground.T if vertical else foreground
    valid = canonical.any(axis=0)
    starts = np.flatnonzero(valid)
    first, last = int(starts[0]), int(starts[-1])+1
    rows = np.arange(canonical.shape[0])[:, None]
    lower = np.where(canonical, rows, -1).max(axis=0)
    upper = np.where(canonical, rows, canonical.shape[0]).min(axis=0)
    span = np.where(valid, lower-upper+1, 0).astype(np.float32)
    length = last-first
    radius = max(1, int(length*.008))
    kernel_x = np.arange(-3*radius, 3*radius+1)
    kernel = np.exp(-.5*(kernel_x/max(1,radius))**2)
    kernel /= kernel.sum()
    smooth = np.convolve(span, kernel, mode="same")
    wide = smooth >= smooth.max()*.50
    indices = np.flatnonzero(wide)
    max_gap = max(3, int(length*.10))
    for a, b in zip(indices[:-1], indices[1:]):
        if 1 < b-a <= max_gap:
            wide[a:b+1] = True
    seed = int(np.argmax(smooth))
    a, b = seed, seed+1
    while a > first and wide[a-1]:
        a -= 1
    while b < last and wide[b]:
        b += 1
    margin = max(2, int(length*.025))
    a, b = max(first,a-margin), min(last,b+margin)
    # A pointed bowl end can be thin too. Only the side with most outlying
    # foreground mass is treated as the slender extension; preserve the far
    # body end all the way to the silhouette (critical for upright support).
    leading_mass = int(canonical[:, first:a].sum())
    trailing_mass = int(canonical[:, b:last].sum())
    if trailing_mass >= leading_mass:
        a = first
    else:
        b = last
    body = np.zeros_like(canonical)
    body[:, a:b] = canonical[:, a:b]
    if vertical:
        body = body.T
    extension = foreground & ~body
    return body, extension, {
        "body_detection": "silhouette-thickness",
        "body_bbox_xyxy": bbox(body),
        "thickness_axis": "vertical" if vertical else "horizontal",
        "body_axis_range": [a,b],
        "maximum_cross_section": round(float(smooth.max()),2),
    }


def extract_masks(rgba, mask_path=None, background=None):
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]
    transparent = bool((alpha < 255).any())
    if mask_path:
        supplied = Image.open(mask_path).convert("L")
        if supplied.size != (rgb.shape[1], rgb.shape[0]):
            raise ValueError("Mask dimensions must equal the oriented input image dimensions.")
        raw = np.asarray(supplied) > 8
        foreground, fraction = largest_component(raw)
        protected = foreground | (np.asarray(supplied) > 0)
        bg = np.asarray(background or (235, 231, 222), dtype=np.float32)
        noise = 0.0
        confidence = "user-mask"
    elif transparent:
        foreground, fraction = largest_component(alpha > 16)
        protected = alpha > 0
        bg = np.asarray(background or (235, 231, 222), dtype=np.float32)
        noise = 0.0
        confidence = "source-alpha"
    else:
        bg, noise, border_match = estimate_background(rgb)
        distance = np.linalg.norm(rgb.astype(np.float32) - bg, axis=2)
        # Do not use a faint old contact shadow as the object's lowest point.
        # Enclosed bright object details are restored by hole filling below.
        threshold = max(36.0, noise * 2.5 + 20)
        foreground, fraction = largest_component(distance > threshold)
        # Wood highlights may have the same RGB as the plain background. Fill
        # enclosed low-contrast regions so they are never shaded as background.
        outside, _ = largest_component(~foreground)
        foreground |= ~outside
        # Do not mistake JPEG ringing in the background for protected product.
        # The connected foreground (including its visible edge) is immutable.
        protected = foreground.copy()
        confidence = "uniform-background"
        if fraction < 0.90:
            raise ValueError("Multiple foreground objects / marks detected; supply a single-pipe mask.")
    occupancy = float(foreground.mean())
    if not 0.005 <= occupancy <= 0.80:
        raise ValueError("Foreground size is implausible for a single pipe; check background / mask.")
    wood, stem, shape_info = shape_parts(foreground)
    # Preserve a well-supported material split for familiar polished pipes,
    # but only after it agrees with the color-independent bulky-body mask.
    # Brown shank + black bowl fails this coverage check by a wide margin.
    rgbf = rgb.astype(np.float32)
    warm = (foreground & (rgbf[:,:,0]-rgbf[:,:,2] > 18)
            & (rgbf[:,:,0]-rgbf[:,:,1] > 7) & (rgbf[:,:,0] > 65))
    if warm.sum() >= foreground.sum()*.10:
        warm = (np.asarray(as_image(warm).filter(ImageFilter.MaxFilter(3))) > 0) & foreground
        warm, _ = largest_component(warm)
        coverage = float((warm & wood).sum()/max(1,int(wood.sum())))
        support_error = abs(bbox(warm)[3]-bbox(wood)[3])
        shape_info["material_body_coverage"] = round(coverage,4)
        if coverage >= .82 and support_error <= max(2,(bbox(foreground)[3]-bbox(foreground)[1])*.012):
            wood, stem = warm, foreground & ~warm
            shape_info["body_detection"] = "material-validated-by-silhouette"
            shape_info["body_bbox_xyxy"] = bbox(wood)
    return foreground, protected, wood, stem, {
        "segmentation": confidence, "background_rgb": [int(v) for v in bg],
        "background_noise": noise, "foreground_fraction": occupancy,
        "largest_component_fraction": round(float(fraction), 6),
        "transparent_input": transparent,
        **shape_info,
    }


def project(a, ground, depth, shear, offset):
    """Compress a side-view silhouette onto a common projected surface."""
    intercept = ground * (1 - depth) + offset
    inverse = (1, shear / depth, -shear * intercept / depth - shear * ground,
               0, 1 / depth, -intercept / depth)
    im = as_image(a)
    return np.asarray(im.transform(im.size, Image.Transform.AFFINE, inverse,
                                   resample=Image.Resampling.BICUBIC), dtype=np.float32) / 255


def combine(*layers):
    transmittance = np.ones_like(layers[0], dtype=np.float32)
    for layer in layers:
        transmittance *= 1 - np.clip(layer, 0, 0.95)
    return 1 - transmittance


def calibrated_shadows(wood, stem, scale, options):
    """A measured soft footprint, adapted to body/support/mouthpiece anchors.

    The stored Gaussian density field is measured from the accepted reference,
    not a stock oval pasted below the object. No reference image is read here.
    """
    style_path = Path(__file__).resolve().parents[1] / "assets" / "shadow-style.json"
    style = json.loads(style_path.read_text(encoding="utf-8"))
    left, top, right, bottom = bbox(wood)
    body_width, body_height = right-left, bottom-top
    ground = float(options.get("ground_y", bottom-1))
    stem_y, stem_x = np.where(stem)
    direction = 1 if not len(stem_x) or np.median(stem_x) > (left+right)/2 else -1
    far_edge = left if direction == 1 else right
    neck = right if direction == 1 else left
    stem_length = max(12*scale, float(np.max(direction*(stem_x-neck)))) if len(stem_x) else 0
    stem_elevation = max(15*scale, ground-float(np.median(stem_y))) if len(stem_y) else body_height*.65
    bottom_by_x = np.where(wood | stem, np.arange(wood.shape[0])[:,None], -1).max(axis=0)
    near_contact = np.flatnonzero(bottom_by_x >= ground-max(3*scale, body_height*.018))
    support_x = float(np.median(near_contact)) if len(near_contact) else (left+right)/2
    support_fraction = float(np.clip(direction*(support_x-far_edge)/body_width, .18, .86))
    reference_support = .70
    softness = float(options.get("softness", 1.0))
    yy, xx = np.indices(wood.shape, dtype=np.float32)
    density_field = np.zeros(wood.shape, dtype=np.float32)
    for component in style["components"]:
        stem_component = component["region"] == "stem"
        if stem_component and not len(stem_x):
            continue
        if stem_component:
            cx = neck + direction*component["x"]*stem_length
            cy = ground + component["y"]*stem_elevation
            sx = component["sigma_x"]*stem_length
            sy = max(component["sigma_y"]*stem_elevation,
                     body_height*.045+stem_elevation*.035)
            amplitude = component["density"] * float(options.get("stem_strength", 1.0))
        else:
            u = component["x"]
            if u < reference_support:
                mapped = u * support_fraction/reference_support
                stretch = support_fraction/reference_support
            else:
                mapped = support_fraction+(u-reference_support)*(1-support_fraction)/(1-reference_support)
                stretch = (1-support_fraction)/(1-reference_support)
            cx = far_edge + direction*mapped*body_width
            cy = ground + component["y"]*body_height
            sx = component["sigma_x"]*body_width*np.clip(stretch, .70, 1.40)
            sy = component["sigma_y"]*body_height
            amplitude = component["density"]
        sx, sy = max(2*scale, sx*softness), max(2*scale, sy*softness)
        density_field += amplitude*np.exp(-.5*((xx-cx)/sx)**2-.5*((yy-cy)/sy)**2)
    cast = 1-np.exp(-density_field)
    # Local contact stays tied to the actual contour, rather than to the style.
    reach = max(3*scale, body_height*.028)
    contact_mask = np.zeros(wood.shape, dtype=np.float32)
    for x in np.flatnonzero(bottom_by_x >= ground-reach*1.4):
        y = int(bottom_by_x[x])
        contact_mask[y:min(wood.shape[0], y+max(2,int(5*scale))),x] = math.exp(-((ground-y)/reach)**2)
    contact = blur(contact_mask, 2.1*scale*max(.7,softness))*.52
    return np.maximum(cast, contact), {
        "ground_y": ground, "support_x": support_x, "stem_direction": direction,
        "stem_length": stem_length, "stem_elevation_proxy": stem_elevation,
        "style": style["name"], "calibration_rmse": style["calibration_rmse"],
    }


def side_shadows(wood, stem, scale, options):
    h, w = wood.shape
    ys = np.where(wood)[0]
    ground = float(options.get("ground_y", ys.max()))
    body_height = max(12.0, float(ys.max() - ys.min()))
    depth, shear = float(options.get("depth", 0.18)), float(options.get("light_x", 0.06))
    softness = float(options.get("softness", 1.0))
    stem_strength = float(options.get("stem_strength", 1.0))
    offset = 7 * scale
    height = np.maximum(0, ground - np.arange(h, dtype=np.float32))[:, None]
    max_height = max(1, float(height[(wood | stem).any(axis=1)].max()))
    cast = np.zeros((h, w), dtype=np.float32)
    # Overlapping height bands give a continuous near-to-far penumbra.
    spacing = max_height / 5
    for i in range(6):
        elevation = i * spacing
        weight = np.maximum(0, 1 - abs(height - elevation) / max(1, spacing))
        attenuation = math.exp(-0.55 * elevation / body_height)
        for mask, density, extra_blur in ((wood, 0.26, 0),
                                           (stem, 0.85 * stem_strength, 6 * scale)):
            projected = project(mask * weight, ground, depth, shear, offset)
            radius = (8 * scale + 0.060 * elevation + extra_blur) * softness
            cast += blur(projected, radius) * density * attenuation
    ambient = blur(project(wood, ground, 0.12, shear * 0.4, 9 * scale),
                   23 * scale * softness) * 0.26
    bottom = np.where(wood | stem, np.arange(h)[:, None], -1).max(axis=0)
    reach = max(4 * scale, body_height * 0.034)
    support = (bottom >= ground - reach * 1.6) & (bottom >= 0)
    contact_mask = np.zeros((h, w), dtype=np.float32)
    thickness = max(2, int(round(4.5 * scale)))
    for x in np.where(support)[0]:
        value = math.exp(-((ground - bottom[x]) / reach) ** 2)
        y = int(bottom[x])
        contact_mask[y:min(h, y + thickness + 1), x] = value
    contact = blur(contact_mask, 2.3 * scale * max(0.7, softness)) * 0.56
    return combine(cast, ambient, contact), {
        "ground_y": round(ground, 2), "projection_depth": depth,
        "projection_shear": shear, "support_columns": int(support.sum()),
    }


def upright_shadows(wood, stem, scale, options):
    """A vertical composition meets the tabletop at its lowest silhouette.

    Screen height is not treated as a row of contact points: the upper wood
    and mouthpiece must not receive an outline / drop shadow beside them.
    """
    left, top, right, bottom = bbox(wood)
    body_width, body_height = right-left, bottom-top
    ground = float(options.get("ground_y", bottom-1))
    bottom_by_x = np.where(wood | stem, np.arange(wood.shape[0])[:, None], -1).max(axis=0)
    reach = max(2*scale, body_height*.012)
    support = np.flatnonzero(bottom_by_x >= ground-reach)
    if not len(support):
        # A deliberately lowered support plane must still use the real foot x.
        support = np.flatnonzero(bottom_by_x >= bottom_by_x.max()-reach)
    support_x = float(np.median(support))
    contact_width = max(body_width*.14, float(np.ptp(support)+1))
    softness = float(options.get("softness", 1.0))
    yy, xx = np.indices(wood.shape, dtype=np.float32)

    def ellipse(cx, cy, sx, sy, density):
        return density*np.exp(-.5*((xx-cx)/max(1,sx))**2
                              -.5*((yy-cy)/max(.75,sy))**2)

    # Three scales share the same physical foot: tight contact, soft occupied
    # tabletop area, then a faint rightward cast shadow. None follows the stem.
    contact = ellipse(support_x, ground+max(.6,body_width*.006),
                      max(3*scale,contact_width*.42), max(1.2,body_width*.014), .50)
    ambient = ellipse(support_x+body_width*.06, ground+body_width*.035,
                      body_width*.25*softness, body_width*.055*softness, .27)
    cast = ellipse(support_x+body_width*.24, ground+body_width*.055,
                   body_width*.36*softness, body_width*.080*softness,
                   .085*float(options.get("stem_strength", 1.0)))
    opacity = 1-np.exp(-(contact+ambient+cast))
    return opacity, {
        "ground_y": ground, "support_x": support_x,
        "support_columns": int(len(support)), "body_width": body_width,
        "contact_width": contact_width, "footprint_center_xy":
            [support_x+body_width*.06, ground+body_width*.035],
        "placement": "lowest-foreground-support",
    }


def clean_support_background(source, protected, body, info, scale, options):
    """Replace only faint neutral old-shadow residue beside the support plane.

    Limited to confidently uniform opaque backgrounds. Strong edges and a
    two-pixel guard around the complete foreground remain immutable.
    """
    if (info["segmentation"] != "uniform-background"
            or options.get("keep_existing_shadows", False)):
        return source, 0
    bg = np.asarray(info["background_rgb"], dtype=np.float32)
    rgb = source[:,:,:3].astype(np.float32)
    difference = bg-rgb
    distance = np.linalg.norm(difference, axis=2)
    guarded = np.asarray(as_image(protected).filter(ImageFilter.MaxFilter(5))) > 0
    left, top, right, bottom = bbox(body)
    ground = float(options.get("ground_y", bottom-1))
    body_height = bottom-top
    fg_left, _, fg_right, _ = bbox(protected)
    yy, xx = np.indices(protected.shape)
    near_support = ((yy >= ground-body_height*.075-2*scale)
                    & (yy <= ground+body_height*.18+5*scale)
                    & (xx >= fg_left-12*scale) & (xx <= fg_right+12*scale))
    neutral_darkening = ((difference.min(axis=2) > 1)
                         & (np.ptp(difference, axis=2) < 6)
                         & (distance < 36))
    cleanup = near_support & neutral_darkening & ~guarded
    cleaned = source.copy()
    cleaned[cleanup,:3] = bg.astype(np.uint8)
    return cleaned, int(cleanup.sum())


def render(image, options=None, mask_path=None, include_base=False):
    options = options or {}
    oriented = ImageOps.exif_transpose(image)
    source = np.array(oriented.convert("RGBA"))
    if "ground_y" in options and not 0 <= float(options["ground_y"]) < source.shape[0]:
        raise ValueError("ground_y must be inside the oriented image height")
    fg, protected, wood, stem, report = extract_masks(
        source, mask_path, options.get("background"))
    bounds = bbox(fg)
    longest = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    scale = longest / 1000.0
    # Every camera angle uses one screen-space tabletop: the lowest point of
    # the complete object, including a mouthpiece that extends below the bowl.
    render_options = {**options, "ground_y": float(options.get("ground_y", bounds[3]-1))}
    view = options.get("view", "auto")
    if view in ("auto", "flat"):
        # Accept old configs, but never restore the former all-around halo.
        view = "upright" if bounds[3] - bounds[1] > (bounds[2] - bounds[0]) * 1.25 else "side"
    if view == "side":
        if options.get("method", "calibrated") == "calibrated":
            opacity, geometry = calibrated_shadows(wood, stem, scale, render_options)
        else:
            opacity, geometry = side_shadows(wood, stem, scale, render_options)
    elif view == "upright":
        opacity, geometry = upright_shadows(wood, stem, scale, render_options)
    else:
        raise ValueError("view must be auto, side, flat or upright")
    opacity = np.clip(opacity * float(options.get("strength", 1.0)), 0, 0.82)
    # An explicit invariant: no shadow is painted over any protected product pixel.
    opacity[protected] = 0
    color = np.array((48, 43, 36), dtype=np.float32)
    overlay = np.empty_like(source)
    overlay[:, :, :3] = color.astype(np.uint8)
    overlay[:, :, 3] = np.uint8(opacity * 255 + 0.5)
    shadow_layer = Image.fromarray(overlay)
    composite_base, cleaned_pixels = clean_support_background(
        source, protected, wood, report, scale, render_options)
    source_im = Image.fromarray(composite_base)
    if report["transparent_input"]:
        if options.get("background"):
            base = Image.new("RGBA", source_im.size, tuple(options["background"]) + (255,))
            base = Image.alpha_composite(base, shadow_layer)
            result = Image.alpha_composite(base, source_im).convert("RGB")
        else:
            result = Image.alpha_composite(shadow_layer, source_im)
    else:
        result = Image.alpha_composite(source_im, shadow_layer).convert("RGB")
    output_array = np.asarray(result.convert("RGBA"))
    opaque_product = protected & (source[:, :, 3] == 255)
    changed = np.any(output_array[:, :, :3] != source[:, :, :3], axis=2)
    altered_product = int((changed & opaque_product).sum())
    if altered_product:
        raise AssertionError("Product preservation invariant failed.")
    edges = np.concatenate((opacity[0], opacity[-1], opacity[:, 0], opacity[:, -1]))
    warnings = []
    if options.get("view") == "flat":
        warnings.append("Legacy flat mode now uses grounded auto placement at the lowest foreground point.")
    if float(edges.max()) > 0.025:
        warnings.append("Shadow approaches the canvas edge. Consider more source margin.")
    if bounds[0] <= 1 or bounds[1] <= 1 or bounds[2] >= source.shape[1] - 1 or bounds[3] >= source.shape[0] - 1:
        warnings.append("The pipe touches the canvas border; check the source crop.")
    report.update({
        "version": VERSION, "view_used": view, "view_requested": options.get("view", "auto"),
        "placement_rule": "lowest-foreground-point-on-tabletop",
        "foreground_lowest_y": bounds[3]-1,
        "ground_y_source": "explicit-override" if "ground_y" in options else "foreground-lowest-point",
        "bbox_xyxy": bounds, "size_wh": list(result.size),
        "foreground_changed_pixels": altered_product,
        "protected_foreground_pixels": int(opaque_product.sum()),
        "changed_background_pixels": int((changed & ~protected).sum()),
        "old_background_shadow_pixels_cleaned": cleaned_pixels,
        "shadow_max_opacity": round(float(opacity.max()), 4),
        "options": options, "geometry": geometry, "warnings": warnings,
        "method": options.get("method", "calibrated") if view == "side" else "upright-footprint",
        "offline": True,
    })
    rendered = (result, shadow_layer, as_image(protected), report)
    if include_base:
        cleaned_base = Image.fromarray(composite_base)
        if not report["transparent_input"]:
            cleaned_base = cleaned_base.convert("RGB")
        return (*rendered, cleaned_base)
    return rendered


def numeric_options(options):
    if not isinstance(options, dict):
        raise ValueError("Image options must be a JSON object")
    limits = {"strength": (0.1, 2.0), "stem_strength": (0.1, 2.5),
              "softness": (0.4, 2.0), "depth": (0.06, 0.45), "light_x": (-0.3, 0.3)}
    allowed = set(limits) | {"view", "method", "ground_y", "background", "keep_existing_shadows"}
    unknown = set(options)-allowed
    if unknown:
        raise ValueError("Unknown option(s): " + ", ".join(sorted(unknown)))
    for key, (low, high) in limits.items():
        if key in options:
            if isinstance(options[key], bool):
                raise ValueError(f"{key} must be a number")
            value = float(options[key])
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
    if "ground_y" in options and (isinstance(options["ground_y"], bool)
            or not math.isfinite(float(options["ground_y"]))):
        raise ValueError("ground_y must be a finite number")
    if "background" in options:
        color = options["background"]
        if (not isinstance(color, (list, tuple)) or len(color) != 3
                or any(isinstance(c, bool) or not isinstance(c, (int, np.integer))
                       or not 0 <= c <= 255 for c in color)):
            raise ValueError("background must contain three integer RGB values from 0 to 255")
    if "keep_existing_shadows" in options and not isinstance(options["keep_existing_shadows"], bool):
        raise ValueError("keep_existing_shadows must be true or false")
    if not isinstance(options.get("view", "auto"), str):
        raise ValueError("view must be auto, side, flat or upright")
    if options.get("view", "auto") not in {"auto", "side", "flat", "upright"}:
        raise ValueError("view must be auto, side, flat or upright")
    if not isinstance(options.get("method", "calibrated"), str):
        raise ValueError("method must be calibrated or projected")
    if options.get("method", "calibrated") not in {"calibrated", "projected"}:
        raise ValueError("method must be calibrated or projected")
    return options


def output_paths(source, output_dir, debug=False):
    paths = {"image": output_dir / f"{source.stem}_shadow.png",
             "layer": output_dir / f"{source.stem}_shadow-only.png",
             "base": output_dir / f"{source.stem}_cleaned-base.png",
             "report": output_dir / f"{source.stem}_shadow-report.json"}
    if debug:
        paths["mask"] = output_dir / f"{source.stem}_protected-mask.png"
    return paths


def same_file_or_path(a, b):
    a, b = Path(a), Path(b)
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))


def artifact_metadata(role):
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text(ARTIFACT_KEY, json.dumps({"generator": "tabletop-shadow", "version": VERSION, "role": role}))
    return metadata


def is_generated_image(path, legacy_cache=None):
    """Use provenance, never a filename fragment, to filter batch artifacts."""
    path = Path(path)
    if path.suffix.lower() != ".png":
        return False
    try:
        with Image.open(path) as image:
            marker = json.loads(image.info.get(ARTIFACT_KEY, "null"))
        if isinstance(marker, dict) and marker.get("generator") == "tabletop-shadow" and marker.get("role") in {"image", "layer", "base", "mask"}:
            return True
    except (OSError, ValueError):
        pass
    if legacy_cache is None:
        legacy_cache = {}
    parent = path.parent.resolve()
    if parent not in legacy_cache:
        names = set()
        for report_path in parent.glob("*_shadow-report.json"):
            try:
                report = json.loads(report_path.read_text(encoding="utf-8-sig"))
                if (not isinstance(report, dict) or report.get("offline") is not True
                        or report.get("saved_png_verified") is not True
                        or not isinstance(report.get("outputs"), dict)
                        or not isinstance(report.get("source_sha256"), str)
                        or len(report["source_sha256"]) != 64):
                    continue
                for role in ("image", "layer", "base", "mask"):
                    value = report["outputs"].get(role)
                    if isinstance(value, str):
                        names.add(PureWindowsPath(value).name.casefold())
            except (OSError, ValueError):
                continue
        legacy_cache[parent] = names
    return path.name.casefold() in legacy_cache[parent]


def process_file(source, output_dir, options=None, mask_path=None, overwrite=False, debug=False):
    source, output_dir = Path(source), Path(output_dir)
    paths = output_paths(source, output_dir, debug)
    readonly_inputs = [source] + ([Path(mask_path)] if mask_path else [])
    for path in paths.values():
        if any(same_file_or_path(path, original) for original in readonly_inputs):
            raise ValueError("An output aliases a source image or mask; input files can never be overwritten.")
        if path.exists() and not overwrite:
            raise FileExistsError(f"Output exists (choose a new output directory): {path}")
    options = numeric_options(dict(options or {}))
    source_bytes = source.read_bytes()
    with Image.open(io.BytesIO(source_bytes)) as image:
        result, layer, protected, report, cleaned_base = render(image, options, mask_path, include_base=True)
        profile = image.info.get("icc_profile")
    if not report["old_background_shadow_pixels_cleaned"]:
        paths.pop("base")
    report["input"] = str(source.resolve())
    report["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    report["outputs"] = {key: str(path.resolve()) for key, path in paths.items()}
    output_dir.mkdir(parents=True, exist_ok=True)
    save_options = {"icc_profile": profile} if profile else {}
    result.save(paths["image"], pnginfo=artifact_metadata("image"), **save_options)
    layer.save(paths["layer"], pnginfo=artifact_metadata("layer"), **save_options)
    if "base" in paths:
        cleaned_base.save(paths["base"], pnginfo=artifact_metadata("base"), **save_options)
    if debug:
        protected.save(paths["mask"], pnginfo=artifact_metadata("mask"))
    # Verify the saved PNG as well as the in-memory result.
    with Image.open(paths["image"]) as saved:
        if saved.size != result.size or not np.array_equal(np.asarray(saved), np.asarray(result)):
            raise AssertionError("Saved PNG does not match the lossless result.")
    report["saved_png_verified"] = True
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="One image or a folder of images")
    parser.add_argument("--output", required=True, type=Path, help="Output folder; originals are never replaced")
    parser.add_argument("--view", choices=("auto", "side", "flat", "upright"), default=None,
                        help="All modes rest on the lowest foreground point; flat is a legacy alias for auto")
    parser.add_argument("--method", choices=("calibrated", "projected"), default=None,
                        help="Default: calibrated soft footprint measured from the approved style")
    parser.add_argument("--strength", type=float, default=None, help="Overall shadow, default 1.0")
    parser.add_argument("--stem-strength", type=float, default=None, help="Mouthpiece cast shadow, default 1.0")
    parser.add_argument("--softness", type=float, default=None, help="Penumbra multiplier, default 1.0")
    parser.add_argument("--depth", type=float, default=None, help="Side-view projected depth, default 0.18")
    parser.add_argument("--light-x", type=float, default=None, help="Side-view horizontal projection shear")
    parser.add_argument("--ground-y", type=float, default=None, help="Override the side-view support y coordinate")
    parser.add_argument("--mask", type=Path, help="One image only; white pipe / black background PNG")
    parser.add_argument("--keep-existing-shadows", action="store_true", default=None,
                        help="Do not clean faint neutral old-shadow residue near the support")
    parser.add_argument("--config", type=Path, help="JSON with defaults and files keyed by relative image path")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--debug", action="store_true", help="Also save the protected product mask")
    parser.add_argument("--overwrite", action="store_true", help="Replace previous generated outputs only")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else {}
        if (not isinstance(config, dict) or set(config)-{"defaults", "files"}
                or not isinstance(config.get("defaults", {}), dict)
                or not isinstance(config.get("files", {}), dict)):
            raise ValueError("Configuration must contain defaults/files JSON objects only")
        defaults = numeric_options(dict(config.get("defaults", {})))
        for key, value in config.get("files", {}).items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise ValueError("Each file override must be a named JSON object")
            numeric_options(dict(value))
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    cli = {k: getattr(args, k) for k in ("view", "method", "strength", "stem_strength", "softness", "depth", "light_x", "ground_y", "keep_existing_shadows")
           if getattr(args, k) is not None}
    skipped_generated = 0
    if args.input.is_file():
        files, root = [args.input], args.input.parent
    elif args.input.is_dir():
        root = args.input
        candidates = root.rglob("*") if args.recursive else root.iterdir()
        legacy_cache = {}
        files = []
        for path in sorted(candidates):
            if (not path.is_file() or path.suffix.lower() not in EXTENSIONS
                    or path.resolve().is_relative_to(args.output.resolve())):
                continue
            if is_generated_image(path, legacy_cache):
                skipped_generated += 1
            else:
                files.append(path)
    else:
        parser.error(f"Input does not exist: {args.input}")
    if not files:
        parser.error("No input images found")
    if args.mask and len(files) != 1:
        parser.error("--mask is only supported for a single input image")
    if args.mask and not args.mask.is_file():
        parser.error(f"Mask does not exist: {args.mask}")
    # Preflight the full plan so a collision cannot silently discard an earlier
    # result, or replace another input, even with --overwrite.
    planned = {}
    readonly_inputs = files + ([args.mask] if args.mask else []) + ([args.config] if args.config else [])
    readonly_paths = {os.path.normcase(str(p.resolve())) for p in readonly_inputs}
    try:
        readonly_ids = {(p.stat().st_dev, p.stat().st_ino) for p in readonly_inputs}
        for path in files:
            relative = path.relative_to(root)
            numeric_options({**defaults, **config.get("files", {}).get(relative.as_posix(), {}), **cli})
            for destination in output_paths(path, args.output/relative.parent, args.debug).values():
                key = os.path.normcase(str(destination.resolve()))
                if key in planned:
                    raise ValueError(f"Output collision between {planned[key]} and {path}; rename the inputs or use separate output folders")
                planned[key] = path
                if key in readonly_paths:
                    raise ValueError(f"Output would overwrite an input: {destination}")
                if destination.exists():
                    st = destination.stat()
                    if (st.st_dev, st.st_ino) in readonly_ids:
                        raise ValueError(f"Output aliases a source image, mask or config: {destination}")
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"event": "batch", "image_count": len(files), "skipped_generated": skipped_generated}, ensure_ascii=False))
    summary = []
    for path in files:
        relative = path.relative_to(root)
        options = {**defaults, **config.get("files", {}).get(relative.as_posix(), {}), **cli}
        try:
            report = process_file(path, args.output / relative.parent, options,
                                  args.mask, args.overwrite, args.debug)
            item = {"input": str(path), "output": report["outputs"]["image"],
                    "view": report["view_used"], "foreground_changed_pixels": 0,
                    "warnings": report["warnings"], "ok": True}
        except Exception as exc:
            item = {"input": str(path), "ok": False, "error": str(exc)}
        summary.append(item)
        print(json.dumps(item, ensure_ascii=False))
    return 0 if all(item["ok"] for item in summary) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
