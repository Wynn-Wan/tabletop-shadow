"""Behavioral regression tests; synthetic fixtures only, no downloads."""
import hashlib
import contextlib
import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from add_shadow import extract_masks, main, process_file, render


def fixture(transparent=False):
    image = Image.new("RGBA", (360, 210), (235, 231, 222, 0 if transparent else 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 65, 215, 165), fill=(170, 85, 35, 255))
    draw.polygon([(195, 80), (310, 35), (322, 50), (212, 115)], fill=(15, 18, 25, 255))
    # A high-reflectance spot deliberately matches the background exactly.
    draw.ellipse((100, 100, 120, 116), fill=(235, 231, 222, 255))
    return image


def upright_fixture():
    image = Image.new("RGB", (360, 420), (235, 231, 222))
    draw = ImageDraw.Draw(image)
    draw.polygon([(167, 35), (190, 35), (199, 145), (160, 145)], fill=(15, 18, 25))
    draw.ellipse((115, 110, 245, 300), fill=(170, 85, 35))
    draw.polygon([(125, 245), (238, 245), (178, 340)], fill=(170, 85, 35))
    return image


class ShadowTests(unittest.TestCase):
    def test_same_stem_batch_is_rejected_before_writing_even_with_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"input";source.mkdir()
            fixture().convert("RGB").save(source/"same.jpg")
            fixture().convert("RGB").save(source/"same.png")
            for overwrite in (False,True):
                out=root/str(overwrite)
                args=[str(source),"--output",str(out)]+(["--overwrite"] if overwrite else [])
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        main(args)
                self.assertNotEqual(error.exception.code,0)
                self.assertFalse(out.exists())

    def test_output_hardlink_cannot_overwrite_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"original.jpg";out=root/"out";out.mkdir()
            fixture().convert("RGB").save(source)
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            try:
                os.link(source,out/"original_shadow.png")
            except OSError as exc:
                self.skipTest(f"Hardlinks unavailable: {exc}")
            with self.assertRaises(ValueError):
                process_file(source,out,overwrite=True)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
            self.assertFalse((out/"original_shadow-report.json").exists())

    def test_mask_cannot_be_replaced_by_an_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"original.jpg";out=root/"out";out.mkdir()
            fixture().convert("RGB").save(source)
            mask=out/"original_shadow-only.png"
            Image.new("L",(360,210),255).save(mask)
            before=mask.read_bytes()
            with self.assertRaises(ValueError):
                process_file(source,out,mask_path=mask,overwrite=True)
            self.assertEqual(mask.read_bytes(),before)
            self.assertFalse((out/"original_shadow.png").exists())

    def test_batch_skips_tagged_outputs_without_needing_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"input";source.mkdir()
            original=source/"original.jpg";fixture().convert("RGB").save(original)
            report=process_file(original,source/"finished",debug=True)
            Path(report["outputs"]["report"]).unlink()
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                code=main([str(source),"--output",str(root/"second"),"--recursive"])
            events=[json.loads(line) for line in captured.getvalue().splitlines()]
            self.assertEqual(code,0)
            self.assertEqual(events[0]["image_count"],1)
            self.assertGreaterEqual(events[0]["skipped_generated"],3)
            self.assertFalse((root/"second"/"finished").exists())

    def test_batch_recognizes_legacy_outputs_by_their_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"input";source.mkdir()
            original=source/"original.jpg";fixture().convert("RGB").save(original)
            report=process_file(original,source/"finished",debug=True)
            for key,path in report["outputs"].items():
                if key!="report":
                    with Image.open(path) as image:
                        pixels=np.asarray(image).copy()
                    Image.fromarray(pixels).save(path)
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                code=main([str(source),"--output",str(root/"second"),"--recursive"])
            events=[json.loads(line) for line in captured.getvalue().splitlines()]
            self.assertEqual(code,0)
            self.assertEqual(events[0]["image_count"],1)
            self.assertGreaterEqual(events[0]["skipped_generated"],3)

    def test_unknown_and_malformed_options_are_rejected_before_output(self):
        bad_options=[{"stem_strenght":1.8},{"background":[300,-1,1000]},
                     {"background":[1,2]},{"keep_existing_shadows":"false"},
                     {"strength":True},{"softness":float("nan")}]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"original.png";fixture(True).save(source)
            for i,options in enumerate(bad_options):
                out=root/f"out-{i}"
                with self.subTest(options=options):
                    with self.assertRaises((ValueError,TypeError)):
                        process_file(source,out,options)
                    self.assertFalse(out.exists())

    def test_malformed_config_fails_before_batch_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"original.png";fixture().save(source)
            config=root/"options.json";config.write_text('[1,2]',encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main([str(source),"--output",str(root/"out"),"--config",str(config)])
            self.assertNotEqual(error.exception.code,0)
            self.assertFalse((root/"out").exists())

    def test_black_bowl_and_brown_long_shank_use_actual_bowl_support(self):
        image = Image.new("RGB", (500, 280), (235, 231, 222))
        draw = ImageDraw.Draw(image)
        draw.ellipse((30, 60, 170, 220), fill=(20, 22, 23))
        draw.polygon([(160,110),(425,145),(425,166),(160,145)], fill=(145,75,35))
        draw.ellipse((415,139,450,177),fill=(12,14,18))
        draw.rectangle((445,150,470,163),fill=(12,14,18))
        result, layer, protection, report = render(image)
        self.assertEqual(report["body_detection"], "silhouette-thickness")
        self.assertLess(report["body_bbox_xyxy"][2], 210)
        self.assertAlmostEqual(report["geometry"]["ground_y"],220,delta=1)
        self.assertGreater(int(np.asarray(layer)[220:238,60:150,3].max()),10)
        mask=np.asarray(protection)>0
        np.testing.assert_array_equal(np.asarray(result)[mask],np.asarray(image)[mask])

    def test_all_black_body_and_extension_are_separated_by_shape(self):
        image=fixture().convert("RGB")
        a=np.asarray(image).copy()
        fg=np.any(a!=[235,231,222],axis=2)
        a[fg]=[20,22,24]
        _,_,_,report=render(Image.fromarray(a))
        self.assertEqual(report["body_detection"],"silhouette-thickness")
        self.assertLess(report["body_bbox_xyxy"][2],260)
        self.assertGreater(report["geometry"]["ground_y"],160)

    def test_dark_grain_does_not_fragment_bulky_body(self):
        image=fixture().convert("RGB")
        draw=ImageDraw.Draw(image)
        for x in range(55,205,16):
            draw.rectangle((x,80,x+8,150),fill=(18,18,20))
        _,_,_,report=render(image)
        self.assertLess(report["body_bbox_xyxy"][0],55)
        self.assertGreater(report["body_bbox_xyxy"][2],190)
        self.assertAlmostEqual(report["geometry"]["ground_y"],165,delta=1)

    def test_dark_upright_point_is_not_cut_off_by_thickness_threshold(self):
        image=upright_fixture()
        a=np.asarray(image).copy()
        fg=np.any(a!=[235,231,222],axis=2)
        a[fg]=[25,27,29]
        _,layer,_,report=render(Image.fromarray(a))
        self.assertEqual(report["view_used"],"upright")
        self.assertAlmostEqual(report["geometry"]["ground_y"],340,delta=1)
        self.assertGreater(int(np.asarray(layer)[340:360,:,3].max()),10)

    def test_faint_old_shadow_does_not_raise_support_and_cleaned_base_composes(self):
        image=fixture().convert("RGB")
        draw=ImageDraw.Draw(image)
        draw.rectangle((35,169,220,176),fill=(226,222,213))
        result,layer,protection,report,base=render(image,include_base=True)
        self.assertAlmostEqual(report["geometry"]["ground_y"],165,delta=1)
        self.assertGreater(report["old_background_shadow_pixels_cleaned"],0)
        np.testing.assert_array_equal(np.asarray(base)[173,60],[235,231,222])
        rebuilt=Image.alpha_composite(base.convert("RGBA"),layer).convert("RGB")
        np.testing.assert_array_equal(np.asarray(result),np.asarray(rebuilt))
        _,_,_,kept,kept_base=render(image,{"keep_existing_shadows":True},include_base=True)
        self.assertEqual(kept["old_background_shadow_pixels_cleaned"],0)
        np.testing.assert_array_equal(np.asarray(kept_base),np.asarray(image))

    def test_upright_auto_puts_shadow_at_real_base_and_preserves_product(self):
        image = upright_fixture()
        result, layer, protection, report = render(image)
        mask = np.asarray(protection) > 0
        np.testing.assert_array_equal(np.asarray(result)[mask], np.asarray(image)[mask])
        self.assertEqual(report["view_used"], "upright")
        self.assertEqual(report["method"], "upright-footprint")
        self.assertEqual(report["foreground_changed_pixels"], 0)
        self.assertAlmostEqual(report["geometry"]["ground_y"], 340, delta=1)
        self.assertAlmostEqual(report["geometry"]["support_x"], 178, delta=4)
        alpha = np.asarray(layer)[:, :, 3]
        self.assertGreater(int(alpha[340:365, 150:225].max()), 40)
        # Upright mode must not produce a halo beside the upper body / stem.
        self.assertEqual(int(alpha[:270].max()), 0)
        self.assertEqual(result.size, image.size)

    def test_legacy_flat_config_uses_grounded_auto_without_an_upper_halo(self):
        image = upright_fixture()
        _, upright, _, upright_report = render(image, {"view": "upright"})
        _, flat, _, flat_report = render(image, {"view": "flat"})
        self.assertEqual(upright_report["view_used"], "upright")
        self.assertEqual(flat_report["view_used"], "upright")
        self.assertEqual(flat_report["view_requested"], "flat")
        np.testing.assert_array_equal(np.asarray(upright), np.asarray(flat))
        self.assertEqual(int(np.asarray(flat)[:270, :, 3].max()), 0)
        self.assertTrue(flat_report["warnings"])

    def test_complete_foreground_lowest_point_sets_table_even_when_stem_is_lower(self):
        image = Image.new("RGB", (400, 300), (235, 231, 222))
        draw = ImageDraw.Draw(image)
        draw.ellipse((35, 50, 205, 175), fill=(170, 85, 35))
        draw.polygon([(190, 105), (355, 244), (342, 254), (185, 125)], fill=(15, 18, 25))
        for options in ({"view":"side"}, {"view":"side","method":"projected"},
                        {"view":"upright"}, {"view":"flat"}, {}):
            with self.subTest(options=options):
                result,layer,mask,report = render(image, options)
                self.assertLess(report["body_bbox_xyxy"][3], 254)
                self.assertEqual(report["foreground_lowest_y"], 254)
                self.assertEqual(report["geometry"]["ground_y"], 254)
                self.assertGreater(int(np.asarray(layer)[254:267, 333:360, 3].max()), 8)
                self.assertEqual(report["foreground_changed_pixels"], 0)
                protected = np.asarray(mask) > 0
                np.testing.assert_array_equal(np.asarray(result)[protected], np.asarray(image)[protected])

    def test_horizontal_legacy_flat_matches_side_tabletop_placement(self):
        image = fixture()
        _,expected,_,_ = render(image,{"view":"side"})
        _,actual,_,report = render(image,{"view":"flat"})
        self.assertEqual(report["view_used"],"side")
        self.assertEqual(report["geometry"]["ground_y"],165)
        np.testing.assert_array_equal(np.asarray(expected),np.asarray(actual))

    def test_upright_config_routes_through_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/"站立.png"
            upright_fixture().save(source)
            config = root/"views.json"
            config.write_text(json.dumps({"files": {source.name: {"view": "upright"}}}), encoding="utf-8")
            code = main([str(source), "--output", str(root/"out"), "--config", str(config)])
            self.assertEqual(code, 0)
            report = json.loads((root/"out"/"站立_shadow-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["view_used"], "upright")
            self.assertEqual(report["foreground_changed_pixels"], 0)

    def test_product_and_enclosed_highlight_preserved(self):
        image = fixture().convert("RGB")
        source = np.asarray(image)
        result, layer, protection, report = render(image)
        mask = np.asarray(protection) > 0
        self.assertTrue(mask[108, 110])
        np.testing.assert_array_equal(np.asarray(result)[mask], source[mask])
        self.assertEqual(report["foreground_changed_pixels"], 0)
        self.assertGreater(report["changed_background_pixels"], 100)
        np.testing.assert_array_equal(np.asarray(result)[:20, :20], source[:20, :20])
        self.assertEqual(result.size, image.size)
        self.assertEqual(int(np.asarray(layer)[:, :, 3][mask].max()), 0)

    def test_mouthpiece_cast_shadow_is_present(self):
        _, layer, _, _ = render(fixture())
        # Open tabletop between the raised stem and the support plane.
        self.assertGreater(int(np.asarray(layer)[110:165, 235:320, 3].max()), 4)

    def test_strength_changes_shadow_but_not_product(self):
        image = fixture()
        _, faint, _, _ = render(image, {"strength": .8})
        _, heavy, _, _ = render(image, {"strength": 1.15})
        self.assertGreater(int(np.asarray(heavy)[:, :, 3].sum()), int(np.asarray(faint)[:, :, 3].sum()))

    def test_transparent_input_keeps_opaque_product(self):
        image = fixture(True)
        result, _, _, report = render(image, {"view": "flat"})
        a = np.asarray(image)
        self.assertEqual(result.mode, "RGBA")
        np.testing.assert_array_equal(np.asarray(result)[a[:, :, 3] == 255], a[a[:, :, 3] == 255])
        self.assertTrue(report["transparent_input"])
        self.assertGreater(int((np.asarray(result)[:, :, 3] > a[:, :, 3]).sum()), 50)

    def test_blank_input_rejected(self):
        with self.assertRaises(ValueError):
            render(Image.new("RGB", (360, 210), (235, 231, 222)))

    def test_two_objects_rejected(self):
        image = Image.new("RGB", (360, 210), (235, 231, 222))
        draw = ImageDraw.Draw(image)
        draw.ellipse((20, 55, 145, 160), fill=(170, 85, 35))
        draw.ellipse((205, 55, 330, 160), fill=(170, 85, 35))
        with self.assertRaises(ValueError):
            render(image)

    def test_source_never_changed_outputs_never_silently_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "pipe.jpg"
            fixture().convert("RGB").save(source)
            fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch.object(socket, "socket", side_effect=AssertionError("Network attempted")):
                report = process_file(source, root / "out", debug=True)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), fingerprint)
            self.assertTrue(report["saved_png_verified"])
            self.assertTrue(report["offline"])
            with self.assertRaises(FileExistsError):
                process_file(source, root / "out")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), fingerprint)

    def test_recursive_batch_preserves_names_and_excludes_output_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "角度").mkdir()
            fixture().convert("RGB").save(root / "a_shadow_collection.jpg")
            fixture().save(root / "角度" / "烟斗.png")
            output = root / "out"
            output.mkdir()
            fixture().save(output / "old-result.png")
            code = main([str(root), "--output", str(output), "--recursive"])
            self.assertEqual(code, 0)
            self.assertTrue((output / "a_shadow_collection_shadow.png").is_file())
            self.assertTrue((output / "角度" / "烟斗_shadow.png").is_file())
            self.assertFalse((output / "out").exists())

    def test_wrong_mask_size_and_invalid_support_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "wrong.png"
            Image.new("L", (20, 20), 255).save(wrong)
            with self.assertRaises(ValueError):
                render(fixture(), mask_path=wrong)
        with self.assertRaises(ValueError):
            render(fixture(), {"ground_y": -10})


if __name__ == "__main__":
    unittest.main()
