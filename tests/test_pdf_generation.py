import re
import tempfile
import unittest
from pathlib import Path

import pikepdf

from pdfdots.pdf_generation import generate_pdf_pair, output_file_paths


class PdfGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.source_path = self.directory / "source.pdf"
        self.print_path = self.directory / "printer.pdf"
        self.cut_path = self.directory / "cutter.pdf"
        self._create_source_pdf()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_source_pdf(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(
                page_size=(215 * 72 / 25.4, 302 * 72 / 25.4)
            )
            page.contents_add(
                pdf.make_stream(b"0 0 1 rg 10 10 20 20 re f\n")
            )
            pdf.save(self.source_path)

    def test_generates_print_and_cut_files_with_expected_geometry(self) -> None:
        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["MediaBox"],
            "left_right",
            1,
            True,
            2.5,
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            self.assertEqual(len(printer.pages), 1)
            self.assertEqual(len(cutter.pages), 1)
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[2]), 239 * 72 / 25.4, places=4
            )
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[3]), 304 * 72 / 25.4, places=4
            )
            self.assertAlmostEqual(
                float(cutter.pages[0].mediabox[2]), 239 * 72 / 25.4, places=4
            )
            printer_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in printer.pages[0].obj["/Contents"]
            )
            cutter_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in cutter.pages[0].obj["/Contents"]
            )

        self.assertIn(b"0 0 1 rg 10 10 20 20 re f", printer_content)
        self.assertIn(b"q 1 0 0 1 34.015748 2.834646 cm", printer_content)
        self.assertIn(
            b"41.102362 9.92126 595.275591 841.889764 re S",
            cutter_content,
        )
        self.assertEqual(cutter_content.count(b" h f"), 4)
        self.assertIn(b"0 0 0 rg", cutter_content)
        self.assertNotIn(b"0 0 1 rg", cutter_content)

    def test_refuses_rotated_source_pages(self) -> None:
        with pikepdf.Pdf.open(self.source_path, allow_overwriting_input=True) as pdf:
            pdf.pages[0].obj["/Rotate"] = 90
            pdf.save(self.source_path)

        with self.assertRaisesRegex(ValueError, "rotated"):
            generate_pdf_pair(
                self.source_path,
                self.print_path,
                self.cut_path,
                ["MediaBox"],
                "left_right",
                1,
                False,
                2.5,
            )
        self.assertFalse(self.print_path.exists())
        self.assertFalse(self.cut_path.exists())

    def test_true_bleed_expands_print_art_but_preserves_cut_box_and_gap(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(
                page_size=(214 * 72 / 25.4, 301 * 72 / 25.4)
            )
            page.obj["/CropBox"] = pikepdf.Array(
                [
                    2 * 72 / 25.4,
                    2 * 72 / 25.4,
                    212 * 72 / 25.4,
                    299 * 72 / 25.4,
                ]
            )
            page.contents_add(
                pdf.make_stream(b"0 0 1 rg 0 0 214 301 re f\n")
            )
            pdf.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["CropBox"],
            "left_right",
            1,
            False,
            2.5,
            repeat_columns=2,
            repeat_gap_mm=5,
            true_bleed_enabled=True,
            true_bleed_mm=2,
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            print_page = printer.pages[0]
            cut_page = cutter.pages[0]
            self.assertAlmostEqual(
                float(print_page.mediabox[2]) / (72 / 25.4),
                453,
                places=3,
            )
            self.assertAlmostEqual(
                float(print_page.mediabox[3]) / (72 / 25.4),
                303,
                places=3,
            )
            print_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in print_page.obj["/Contents"]
            )
            cut_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in cut_page.obj["/Contents"]
            )
            cut_rectangles = re.findall(
                rb"0 0 0 RG 0\.75 w ([\d.-]+) ([\d.-]+) "
                rb"([\d.-]+) ([\d.-]+) re S",
                cut_content,
            )

        self.assertIn(b"606.614174 853.228347 re W n", print_content)
        self.assertEqual(len(cut_rectangles), 2)
        cut_xs = [float(rectangle[0]) / (72 / 25.4) for rectangle in cut_rectangles]
        cut_widths = [
            float(rectangle[2]) / (72 / 25.4) for rectangle in cut_rectangles
        ]
        self.assertAlmostEqual(cut_widths[0], 210, places=3)
        self.assertAlmostEqual(cut_widths[1], 210, places=3)
        self.assertAlmostEqual(cut_xs[1] - cut_xs[0] - cut_widths[0], 5, places=3)

    def test_true_bleed_works_for_single_layout_page_copies(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(
                page_size=(214 * 72 / 25.4, 301 * 72 / 25.4)
            )
            page.obj["/CropBox"] = pikepdf.Array(
                [
                    2 * 72 / 25.4,
                    2 * 72 / 25.4,
                    212 * 72 / 25.4,
                    299 * 72 / 25.4,
                ]
            )
            page.contents_add(
                pdf.make_stream(b"0 0 1 rg 0 0 214 301 re f\n")
            )
            pdf.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["CropBox"],
            "left_right",
            1,
            False,
            2.5,
            repeat_columns=2,
            repeat_gap_mm=5,
            page_copy_counts=[2],
            true_bleed_enabled=True,
            true_bleed_mm=2,
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            form = printer.pages[0].obj["/Resources"]["/XObject"]["/Page1"]
            form_box = form["/BBox"]
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[2]) / (72 / 25.4),
                453,
                places=3,
            )
            self.assertAlmostEqual(
                (float(form_box[2]) - float(form_box[0])) / (72 / 25.4),
                214,
                places=3,
            )
            cut_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in cutter.pages[0].obj["/Contents"]
            )
            cut_rectangles = re.findall(
                rb"0 0 0 RG 0\.75 w ([\d.-]+) ([\d.-]+) "
                rb"([\d.-]+) ([\d.-]+) re S",
                cut_content,
            )
        self.assertEqual(len(cut_rectangles), 2)
        cut_xs = [float(rectangle[0]) / (72 / 25.4) for rectangle in cut_rectangles]
        cut_widths = [
            float(rectangle[2]) / (72 / 25.4) for rectangle in cut_rectangles
        ]
        self.assertAlmostEqual(cut_xs[1] - cut_xs[0] - cut_widths[0], 5, places=3)

    def test_generates_variable_size_pages_from_per_page_box_choices(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page(page_size=(215 * 72 / 25.4, 302 * 72 / 25.4))
            pdf.add_blank_page(page_size=(210 * 72 / 25.4, 297 * 72 / 25.4))
            pdf.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["MediaBox", "MediaBox"],
            "left_right",
            1,
            False,
            2.5,
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            self.assertEqual(len(printer.pages), 2)
            self.assertEqual(len(cutter.pages), 2)
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[2]), 239 * 72 / 25.4, places=4
            )
            self.assertAlmostEqual(
                float(printer.pages[1].mediabox[2]), 234 * 72 / 25.4, places=4
            )
            self.assertAlmostEqual(
                float(cutter.pages[0].mediabox[3]), 304 * 72 / 25.4, places=4
            )
            self.assertAlmostEqual(
                float(cutter.pages[1].mediabox[3]), 299 * 72 / 25.4, places=4
            )

    def test_repeats_page_grid_with_only_outer_registration_dots(self) -> None:
        with pikepdf.Pdf.open(
            self.source_path,
            allow_overwriting_input=True,
        ) as source:
            annotation = source.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Annot"),
                        "/Subtype": pikepdf.Name("/Link"),
                        "/Rect": pikepdf.Array([10, 10, 20, 20]),
                    }
                )
            )
            source.pages[0].obj["/Annots"] = pikepdf.Array([annotation])
            source.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["MediaBox"],
            "left_right",
            1,
            False,
            2.5,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            self.assertEqual(len(printer.pages), 1)
            self.assertEqual(len(cutter.pages), 1)
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[2]), 459 * 72 / 25.4, places=4
            )
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[3]), 611 * 72 / 25.4, places=4
            )
            printer_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in printer.pages[0].obj["/Contents"]
            )
            cutter_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in cutter.pages[0].obj["/Contents"]
            )
            annotations = list(printer.pages[0].obj["/Annots"])
            annotation_rects = [
                tuple(float(value) for value in annotation["/Rect"])
                for annotation in annotations
            ]

        self.assertEqual(
            printer_content.count(b"0 0 1 rg 10 10 20 20 re f"),
            4,
        )
        self.assertEqual(printer_content.count(b" h f"), 4)
        self.assertEqual(cutter_content.count(b" h f"), 4)
        self.assertEqual(cutter_content.count(b" re S"), 4)
        self.assertEqual(len(annotations), 4)
        self.assertAlmostEqual(
            annotation_rects[1][0] - annotation_rects[0][0],
            220 * 72 / 25.4,
            places=4,
        )
        self.assertAlmostEqual(
            annotation_rects[2][1] - annotation_rects[0][1],
            307 * 72 / 25.4,
            places=4,
        )

    def test_arranges_eight_document_pages_across_two_layouts(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            for page_number in range(8):
                page = pdf.add_blank_page(
                    page_size=(215 * 72 / 25.4, 302 * 72 / 25.4)
                )
                page.obj["/CropBox"] = pikepdf.Array(
                    [0, 0, 210 * 72 / 25.4, 297 * 72 / 25.4]
                )
                page.contents_add(
                    pdf.make_stream(
                        f"0 0 1 rg {page_number + 1} 10 20 20 re f\n".encode()
                    )
                )
            pdf.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["CropBox"] * 8,
            "left_right",
            1,
            False,
            2.5,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
            arrange_document_pages=True,
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            self.assertEqual(len(printer.pages), 2)
            self.assertEqual(len(cutter.pages), 2)
            for layout_index, page in enumerate(printer.pages):
                content = b"\n".join(
                    bytes(stream.read_bytes())
                    for stream in page.obj["/Contents"]
                )
                self.assertEqual(content.count(b" Do Q"), 4)
                self.assertEqual(content.count(b" h f"), 4)
                self.assertAlmostEqual(
                    float(page.mediabox[2]),
                    449 * 72 / 25.4,
                    places=4,
                )
                self.assertAlmostEqual(
                    float(page.mediabox[3]),
                    601 * 72 / 25.4,
                    places=4,
                )
                xobjects = page.obj["/Resources"]["/XObject"]
                self.assertEqual(len(xobjects), 4)
                for page_in_layout in range(4):
                    source_number = layout_index * 4 + page_in_layout + 1
                    form_content = bytes(xobjects[f"/Page{source_number}"].read_bytes())
                    self.assertIn(
                        f"{source_number} 10 20 20 re f".encode(),
                        form_content,
                    )
                    self.assertAlmostEqual(
                        float(xobjects[f"/Page{source_number}"]["/BBox"][2]),
                        210 * 72 / 25.4,
                        places=4,
                    )
            for page in cutter.pages:
                content = b"\n".join(
                    bytes(stream.read_bytes())
                    for stream in page.obj["/Contents"]
                )
                self.assertEqual(content.count(b" re S"), 4)
                self.assertEqual(content.count(b" h f"), 4)

    def test_arranged_final_layout_leaves_unused_slots_blank(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            for _ in range(5):
                pdf.add_blank_page(
                    page_size=(215 * 72 / 25.4, 302 * 72 / 25.4)
                )
            pdf.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["MediaBox"] * 5,
            "left_right",
            1,
            False,
            2.5,
            repeat_columns=2,
            repeat_rows=2,
            arrange_document_pages=True,
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            self.assertEqual(len(printer.pages), 2)
            self.assertEqual(
                len(printer.pages[1].obj["/Resources"]["/XObject"]),
                1,
            )
            final_cutter_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in cutter.pages[1].obj["/Contents"]
            )
            self.assertEqual(final_cutter_content.count(b" re S"), 1)
            self.assertEqual(final_cutter_content.count(b" h f"), 4)

    def test_places_individual_document_page_copies_in_fixed_grid(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            for page_number in range(2):
                page = pdf.add_blank_page(
                    page_size=(215 * 72 / 25.4, 302 * 72 / 25.4)
                )
                page.contents_add(
                    pdf.make_stream(
                        f"0 0 1 rg {page_number + 1} 10 20 20 re f\n".encode()
                    )
                )
            pdf.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["MediaBox", "MediaBox"],
            "left_right",
            1,
            True,
            2.5,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5,
            page_copy_counts=[2, 2],
        )

        with pikepdf.Pdf.open(self.print_path) as printer, pikepdf.Pdf.open(
            self.cut_path
        ) as cutter:
            self.assertEqual(len(printer.pages), 1)
            self.assertEqual(len(cutter.pages), 1)
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[2]), 459 * 72 / 25.4, places=4
            )
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[3]), 611 * 72 / 25.4, places=4
            )
            print_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in printer.pages[0].obj["/Contents"]
            )
            cut_content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in cutter.pages[0].obj["/Contents"]
            )
            cut_rectangles = [
                [float(value) for value in line.split()[-6:-2]]
                for line in cut_content.splitlines()
                if line.endswith(b" re S")
            ]

        self.assertEqual(print_content.count(b" Do Q"), 4)
        self.assertEqual(print_content.count(b" h f"), 4)
        self.assertEqual(cut_content.count(b" re S"), 4)
        for rectangle in cut_rectangles:
            self.assertAlmostEqual(rectangle[2], 210 * 72 / 25.4, places=5)
            self.assertAlmostEqual(rectangle[3], 297 * 72 / 25.4, places=5)
        self.assertAlmostEqual(
            cut_rectangles[0][0],
            14.5 * 72 / 25.4,
            places=5,
        )
        self.assertAlmostEqual(
            cut_rectangles[1][0] - cut_rectangles[0][0],
            220 * 72 / 25.4,
            places=5,
        )
        self.assertAlmostEqual(
            abs(cut_rectangles[0][1] - cut_rectangles[2][1]),
            307 * 72 / 25.4,
            places=5,
        )

    def test_selected_crop_box_clips_source_content_to_that_box(self) -> None:
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page(
                page_size=(215 * 72 / 25.4, 302 * 72 / 25.4)
            )
            page.obj["/CropBox"] = pikepdf.Array(
                [0, 0, 210 * 72 / 25.4, 297 * 72 / 25.4]
            )
            pdf.save(self.source_path)

        generate_pdf_pair(
            self.source_path,
            self.print_path,
            self.cut_path,
            ["CropBox"],
            "left_right",
            1,
            False,
            2.5,
        )

        with pikepdf.Pdf.open(self.print_path) as printer:
            content = b"\n".join(
                bytes(stream.read_bytes())
                for stream in printer.pages[0].obj["/Contents"]
            )
            self.assertAlmostEqual(
                float(printer.pages[0].mediabox[2]), 234 * 72 / 25.4, places=4
            )

        self.assertIn(
            b"0 0 595.275591 841.889764 re W n",
            content,
        )

    def test_names_variable_size_documents_and_sanitizes_description(self) -> None:
        print_path, cut_path = output_file_paths(
            self.source_path,
            self.directory,
            "Job: A/B",
            [(234, 299), (212, 321)],
        )

        self.assertEqual(print_path.name, "source_Job_A_B_PRINT_variable-size.pdf")
        self.assertEqual(cut_path.name, "source_Job_A_B_CUT_variable-size.pdf")

    def test_names_single_size_outputs_with_dimensions(self) -> None:
        print_path, cut_path = output_file_paths(
            self.source_path,
            self.directory,
            "",
            [(234, 299)],
        )

        self.assertEqual(print_path.name, "source_PRINT_234x299mm.pdf")
        self.assertEqual(cut_path.name, "source_CUT_234x299mm.pdf")


if __name__ == "__main__":
    unittest.main()
