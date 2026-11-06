import tempfile
import unittest
from pathlib import Path

import pikepdf

from pdfdots.pdf_inspection import (
    BOX_NAMES,
    BoxDimensions,
    cutter_rectangle_size,
    document_layout_geometries,
    expanded_output_geometry,
    expanded_bleed_box,
    inspect_pdf,
    repeated_document_layout_geometry,
    repeated_output_geometry,
    true_bleed_gap_mm,
)


class PdfInspectionTests(unittest.TestCase):
    def test_inspects_effective_page_boxes_in_millimetres(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "a4.pdf"
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page(page_size=(600, 900))
                page.obj["/CropBox"] = pikepdf.Array([0, 0, 595.2756, 841.8898])
                page.obj["/TrimBox"] = pikepdf.Array([7.0866, 7.0866, 588.189, 834.8032])
                pdf.save(path)

            pages = inspect_pdf(path)

        self.assertEqual(len(pages), 1)
        self.assertEqual(set(pages[0].boxes), set(BOX_NAMES))
        crop_box = pages[0].boxes["CropBox"]
        self.assertAlmostEqual(crop_box.width_mm, 210, places=2)
        self.assertAlmostEqual(crop_box.height_mm, 297, places=2)
        self.assertAlmostEqual(
            pages[0].boxes["TrimBox"].width_mm, 205, places=2
        )

    def test_fake_bleed_insets_cutter_rectangle_equally(self) -> None:
        source_box = BoxDimensions(
            name="CropBox",
            left=0,
            bottom=0,
            right=215 * 72 / 25.4,
            top=302 * 72 / 25.4,
        )

        width, height = cutter_rectangle_size(source_box, 2.5)

        self.assertAlmostEqual(width, 210)
        self.assertAlmostEqual(height, 297)

    def test_fake_bleed_rejects_an_inset_that_consumes_the_page(self) -> None:
        source_box = BoxDimensions("CropBox", 0, 0, 10, 10)

        with self.assertRaisesRegex(ValueError, "too large"):
            cutter_rectangle_size(source_box, 2)

    def test_true_bleed_expands_selected_box_inside_media_box(self) -> None:
        media_box = BoxDimensions(
            "MediaBox",
            0,
            0,
            214 * 72 / 25.4,
            301 * 72 / 25.4,
        )
        crop_box = BoxDimensions(
            "CropBox",
            2 * 72 / 25.4,
            2 * 72 / 25.4,
            212 * 72 / 25.4,
            299 * 72 / 25.4,
        )

        bleed_box = expanded_bleed_box(crop_box, media_box, 2)

        self.assertAlmostEqual(bleed_box.width_mm, 214)
        self.assertAlmostEqual(bleed_box.height_mm, 301)
        self.assertAlmostEqual(bleed_box.left, media_box.left)
        self.assertAlmostEqual(bleed_box.bottom, media_box.bottom)
        self.assertAlmostEqual(bleed_box.right, media_box.right)
        self.assertAlmostEqual(bleed_box.top, media_box.top)

    def test_true_bleed_rejects_missing_artwork_and_page_overlap(self) -> None:
        media_box = BoxDimensions("MediaBox", 0, 0, 212, 299)
        crop_box = BoxDimensions("CropBox", 2, 2, 212, 299)

        with self.assertRaisesRegex(ValueError, "extends beyond the page's MediaBox"):
            expanded_bleed_box(crop_box, media_box, 2)
        with self.assertRaisesRegex(ValueError, "printed pages overlap"):
            true_bleed_gap_mm(3, 2)
        self.assertEqual(true_bleed_gap_mm(5, 2), 1)

    def test_left_right_layout_includes_dots_and_safety_distance(self) -> None:
        source_box = BoxDimensions("CropBox", 0, 0, 210 * 72 / 25.4, 297 * 72 / 25.4)

        geometry = expanded_output_geometry(source_box, "left_right", 1)

        self.assertAlmostEqual(geometry.width_mm, 234)
        self.assertAlmostEqual(geometry.height_mm, 299)
        self.assertAlmostEqual(geometry.source_offset_x_mm, 12)
        self.assertAlmostEqual(geometry.source_offset_y_mm, 1)

    def test_top_bottom_layout_rotates_the_dot_clearance(self) -> None:
        source_box = BoxDimensions("CropBox", 0, 0, 210 * 72 / 25.4, 297 * 72 / 25.4)

        geometry = expanded_output_geometry(source_box, "top_bottom", 1)

        self.assertAlmostEqual(geometry.width_mm, 212)
        self.assertAlmostEqual(geometry.height_mm, 321)
        self.assertAlmostEqual(geometry.source_offset_x_mm, 1)
        self.assertAlmostEqual(geometry.source_offset_y_mm, 12)

    def test_repeated_layout_geometry_includes_inter_page_gaps(self) -> None:
        source_box = BoxDimensions(
            "CropBox",
            0,
            0,
            215 * 72 / 25.4,
            302 * 72 / 25.4,
        )

        geometry = repeated_output_geometry(
            source_box,
            "left_right",
            1,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
        )

        self.assertAlmostEqual(geometry.width_mm, 459)
        self.assertAlmostEqual(geometry.height_mm, 611)

    def test_document_pages_fill_fixed_row_major_layouts(self) -> None:
        boxes = [
            BoxDimensions("CropBox", 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for _ in range(5)
        ]

        layouts = document_layout_geometries(
            boxes,
            "left_right",
            1,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
        )

        self.assertEqual(len(layouts), 2)
        self.assertEqual(
            [placement.page_index for placement in layouts[0].placements],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [placement.page_index for placement in layouts[1].placements],
            [4],
        )
        self.assertAlmostEqual(layouts[0].width_mm, 459)
        self.assertAlmostEqual(layouts[0].height_mm, 611)
        self.assertAlmostEqual(layouts[1].width_mm, layouts[0].width_mm)
        self.assertAlmostEqual(layouts[1].height_mm, layouts[0].height_mm)

    def test_document_layout_reserves_blank_grid_slots(self) -> None:
        box = BoxDimensions("CropBox", 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)

        layouts = document_layout_geometries(
            [box],
            "left_right",
            1,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
        )

        self.assertEqual(len(layouts), 1)
        self.assertEqual(len(layouts[0].placements), 1)
        self.assertAlmostEqual(layouts[0].grid_width_mm, 435)
        self.assertAlmostEqual(layouts[0].grid_height_mm, 609)

    def test_document_layout_centers_different_page_sizes_in_grid_cells(self) -> None:
        boxes = [
            BoxDimensions("CropBox", 0, 0, 210 * 72 / 25.4, 297 * 72 / 25.4),
            BoxDimensions("CropBox", 0, 0, 220 * 72 / 25.4, 300 * 72 / 25.4),
            BoxDimensions("CropBox", 0, 0, 215 * 72 / 25.4, 310 * 72 / 25.4),
            BoxDimensions("CropBox", 0, 0, 200 * 72 / 25.4, 295 * 72 / 25.4),
        ]

        layouts = document_layout_geometries(
            boxes,
            "left_right",
            1,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
        )

        self.assertEqual(len(layouts), 1)
        self.assertAlmostEqual(layouts[0].grid_width_mm, 440)
        self.assertAlmostEqual(layouts[0].grid_height_mm, 615)
        self.assertAlmostEqual(layouts[0].placements[0].offset_x_mm, 2.5)
        self.assertAlmostEqual(layouts[0].placements[0].offset_y_mm, 316.5)
        self.assertAlmostEqual(layouts[0].placements[3].offset_x_mm, 230)
        self.assertAlmostEqual(layouts[0].placements[3].offset_y_mm, 7.5)

    def test_page_copy_counts_fill_fixed_grid_in_page_order(self) -> None:
        boxes = [
            BoxDimensions("CropBox", 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4),
            BoxDimensions("CropBox", 0, 0, 210 * 72 / 25.4, 297 * 72 / 25.4),
            BoxDimensions("CropBox", 0, 0, 220 * 72 / 25.4, 310 * 72 / 25.4),
        ]

        geometry = repeated_document_layout_geometry(
            boxes,
            [2, 2, 4],
            "left_right",
            1,
            repeat_columns=4,
            repeat_rows=2,
            repeat_gap_mm=5,
        )

        self.assertEqual(len(geometry.placements), 8)
        self.assertEqual(
            [placement.page_index for placement in geometry.placements],
            [0, 0, 1, 1, 2, 2, 2, 2],
        )
        self.assertAlmostEqual(geometry.grid_width_mm, 895)
        self.assertAlmostEqual(geometry.grid_height_mm, 617)
        self.assertAlmostEqual(geometry.width_mm, 919)
        self.assertAlmostEqual(geometry.height_mm, 619)
        self.assertAlmostEqual(geometry.placements[0].offset_y_mm, 315)
        self.assertAlmostEqual(geometry.placements[2].offset_x_mm, 455)
        self.assertAlmostEqual(geometry.placements[4].offset_y_mm, 0)

    def test_page_copy_counts_reject_overflow_and_leave_unused_cells(self) -> None:
        box = BoxDimensions("CropBox", 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)

        geometry = repeated_document_layout_geometry(
            [box, box],
            [1, 1],
            "left_right",
            1,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
        )
        self.assertEqual(len(geometry.placements), 2)
        self.assertAlmostEqual(geometry.grid_width_mm, 435)
        self.assertAlmostEqual(geometry.grid_height_mm, 609)

        with self.assertRaisesRegex(ValueError, "only 2"):
            repeated_document_layout_geometry(
                [box, box],
                [2, 1],
                "left_right",
                1,
                repeat_columns=1,
                repeat_rows=2,
                repeat_gap_mm=5,
            )


if __name__ == "__main__":
    unittest.main()
