#!/usr/bin/env python3
"""Rebuild the published before/after previews from bundled photo inputs."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

from add_shadow import process_file

ROOT = Path(__file__).resolve().parents[1]


def main():
    inputs = ROOT/"examples"/"input"
    output = ROOT/"examples"/"output"
    previews = ROOT/"examples"/"previews"
    previews.mkdir(parents=True, exist_ok=True)
    modes = {"side": "side", "bowl-facing": "side", "reverse-side": "side",
             "underside": "side", "upright": "upright"}
    reports = {}
    for name, view in modes.items():
        report = process_file(inputs/f"{name}.png", output, {"view": view},
                              overwrite=True, debug=True)
        assert report["foreground_changed_pixels"] == 0 and report["saved_png_verified"]
        reports[name] = report
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        try:
            font = ImageFont.load_default(size=22)
        except TypeError:
            font = ImageFont.load_default()
    for filename, name in (("side", "side"), ("upright", "upright"), ("bowl-facing", "bowl-facing")):
        canvas = Image.new("RGB", (1200, 384), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((18, 8), "Before", fill="#222222", font=font)
        draw.text((618, 8), f"After: {modes[name]} / offline", fill="#222222", font=font)
        for x, path in ((0, inputs/f"{name}.png"), (600, output/f"{name}_shadow.png")):
            with Image.open(path) as image:
                tile = image.convert("RGB").resize((600, 338), Image.Resampling.LANCZOS)
                canvas.paste(tile, (x, 42))
        canvas.save(previews/f"{filename}.png")
    print(json.dumps({"examples_rendered": len(reports),
                      "foreground_changed_pixels": sum(r["foreground_changed_pixels"] for r in reports.values()),
                      "previews": ["side.png", "upright.png", "bowl-facing.png"]}))


if __name__ == "__main__":
    main()
