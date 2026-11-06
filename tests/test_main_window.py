import gc
import os
import tempfile
import unittest
from unittest.mock import patch

import pikepdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QMimeData, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QComboBox

from pdfdots.main_window import (
    ExpandedPreviewWindow,
    MainWindow,
    PdfDropLineEdit,
)
from pdfdots.pdf_inspection import BOX_NAMES, BoxDimensions, PageDimensions


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_controls_show_fake_bleed_result_and_page_selection(self) -> None:
        window = MainWindow()
        self.assertFalse(window.windowIcon().isNull())
        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        window._pages = [PageDimensions(page_number=1, boxes=boxes)]
        window._populate_dimensions_table()
        window._refresh_summary()

        self.assertEqual(window._safety_distance.value(), 1.0)
        self.assertEqual(window._fake_bleed_inset.value(), 2.5)
        self.assertFalse(window._fake_bleed_inset.isEnabled())

        window._fake_bleed_checkbox.setChecked(True)

        self.assertTrue(window._fake_bleed_inset.isEnabled())
        self.assertIn("210.00 x 297.00 mm", window._summary_label.text())
        self.assertIn("239.00 x 304.00 mm", window._generate_button.text())
        self.assertIn("2.50 mm inset per side", window._live_preview_details.text())

        window._page_by_page_radio.setChecked(True)
        selection = window._dimensions_table.cellWidget(0, len(BOX_NAMES) + 1)
        self.assertIsInstance(selection, QComboBox)
        self.assertTrue(selection.isEnabled())
        self.assertFalse(hasattr(window, "_preview_button"))
        self.assertTrue(window._source_field.acceptDrops())
        self.assertEqual(window._repeat_columns_control.value(), 1)

    def test_live_preview_updates_with_repeat_and_bleed_controls(self) -> None:
        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        window = MainWindow()
        window._pages = [
            PageDimensions(page_number=1, boxes=boxes),
            PageDimensions(page_number=2, boxes=boxes),
        ]
        window._populate_dimensions_table()
        window._repeat_columns_control.setValue(2)
        window._repeat_rows_control.setValue(2)

        window._fake_bleed_checkbox.setChecked(True)

        self.assertIn("2.50 mm inset per side", window._live_preview_details.text())
        window._repeat_pages_checkbox.setChecked(True)
        self.assertIn("Pages 1–2", window._live_preview_details.text())
        self.assertIn("p1 x1, p2 x1", window._live_preview_details.text())
        self.assertIn("One 2 x 2 grid", window._output_size_label.text())
        self.assertFalse(window._arrange_pages_checkbox.isChecked())
        self.assertEqual(
            [tile[5] for tile in window._live_preview_widget._preview[2]],
            [1, 2],
        )
        self.assertTrue(window._dimensions_table.cellWidget(0, len(BOX_NAMES) + 2).isEnabled())

    def test_true_bleed_expands_artwork_but_keeps_cut_box_and_configured_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = os.path.join(temporary_directory, "true-bleed.pdf")
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
                pdf.save(source_path)

            window = MainWindow()
            window._load_source_path(source_path)
            window._repeat_columns_control.setValue(2)
            self.assertEqual(window._true_bleed_checkbox.text(), "True bleed")
            window._true_bleed_checkbox.setChecked(True)

            geometry, _, tiles, *_ = window._live_preview_widget._preview
            self.assertAlmostEqual(geometry.width_mm, 453, places=5)
            self.assertAlmostEqual(geometry.height_mm, 303, places=5)
            self.assertEqual(len(tiles), 2)
            self.assertAlmostEqual(tiles[0][0].width_mm, 214, places=5)
            self.assertAlmostEqual(tiles[0][0].height_mm, 301, places=5)
            self.assertAlmostEqual(tiles[0][3], 210, places=5)
            self.assertAlmostEqual(tiles[0][4], 297, places=5)
            self.assertIsInstance(tiles[0][6], QImage)
            self.assertAlmostEqual(
                tiles[0][6].width() / tiles[0][6].height(),
                214 / 301,
                delta=0.01,
            )
            self.assertAlmostEqual(tiles[1][1] - tiles[0][1], 215, places=5)
            self.assertIn("1.00 mm printed gap", window._live_preview_details.text())
            self.assertIn("5.00 mm gap", window._live_preview_details.text())
            window._close_pdf_preview()
            window.close()
            del window
            gc.collect()

    def test_true_bleed_requires_enough_artwork_outside_selected_box(self) -> None:
        box = BoxDimensions("CropBox", 0, 0, 210 * 72 / 25.4, 297 * 72 / 25.4)
        boxes = {name: box for name in BOX_NAMES}
        window = MainWindow()
        window._pages = [PageDimensions(page_number=1, boxes=boxes)]
        window._populate_dimensions_table()
        window._true_bleed_checkbox.setChecked(True)

        self.assertIn("extends beyond the page's MediaBox", window._summary_label.text())
        self.assertIn(
            "extends beyond the page's MediaBox",
            window._live_preview_details.text(),
        )
        window.close()

    def test_actual_page_preview_renders_pdf_artwork_in_live_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = os.path.join(temporary_directory, "preview.pdf")
            with pikepdf.Pdf.new() as pdf:
                for page_number in range(2):
                    page = pdf.add_blank_page(page_size=(300, 400))
                    page.contents_add(
                        pdf.make_stream(
                            f"1 0 0 rg 20 20 100 100 re f {page_number + 1} 0 0 rg "
                            "140 20 100 100 re f\n".encode()
                        )
                    )
                pdf.save(source_path)

            window = MainWindow()
            window._load_source_path(source_path)

            self.assertTrue(window._actual_preview_checkbox.isEnabled())
            self.assertTrue(window._actual_preview_checkbox.isChecked())
            self.assertIn(1, window._preview_image_cache)
            window._live_preview_selector.setCurrentIndex(1)

            self.assertEqual(set(window._preview_image_cache), {1, 2})
            self.assertTrue(
                all(
                    tile[6] is not None and not tile[6].isNull()
                    for tile in window._live_preview_widget._preview[2]
                )
            )
            window._live_preview_widget.resize(640, 400)
            image = QImage(640, 400, QImage.Format.Format_ARGB32)
            window._live_preview_widget.render(image)
            self.assertIn("actual", window._actual_preview_checkbox.text().lower())

            preview_document = window._pdf_preview_document
            window._close_pdf_preview()
            window.close()
            del window
            del preview_document
            gc.collect()

    def test_preview_crops_selected_box_from_full_page_artwork(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = os.path.join(temporary_directory, "box-preview.pdf")
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page(page_size=(300, 400))
                page.obj["/CropBox"] = pikepdf.Array([50, 60, 250, 360])
                page.obj["/BleedBox"] = pikepdf.Array([20, 30, 280, 380])
                page.contents_add(
                    pdf.make_stream(
                        b"1 0 0 rg 0 0 40 400 re f "
                        b"0 1 0 rg 50 0 80 400 re f "
                        b"0 0 1 rg 260 0 40 400 re f\n"
                    )
                )
                pdf.save(source_path)

            window = MainWindow()
            window._load_source_path(source_path)
            window._page_by_page_radio.setChecked(True)
            box_selection = window._dimensions_table.cellWidget(
                0,
                len(BOX_NAMES) + 1,
            )
            box_selection.setCurrentIndex(box_selection.findData("BleedBox"))

            tile_image = window._live_preview_widget._preview[2][0][6]
            self.assertIsInstance(tile_image, QImage)
            self.assertAlmostEqual(
                tile_image.width() / tile_image.height(),
                260 / 350,
                delta=0.01,
            )
            left_edge = tile_image.pixelColor(4, tile_image.height() // 2)
            right_edge = tile_image.pixelColor(
                tile_image.width() - 5,
                tile_image.height() // 2,
            )
            self.assertGreater(left_edge.red(), 200)
            self.assertLess(left_edge.green(), 40)
            self.assertGreater(right_edge.blue(), 200)
            self.assertLess(right_edge.red(), 40)
            window.close()

    def test_page_copy_counts_fill_configured_grid_and_show_large_page_numbers(self) -> None:
        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        window = MainWindow()
        window._pages = [
            PageDimensions(page_number=index + 1, boxes=boxes)
            for index in range(3)
        ]
        window._populate_dimensions_table()
        window._repeat_columns_control.setValue(4)
        window._repeat_rows_control.setValue(2)
        window._repeat_pages_checkbox.setChecked(True)
        copy_counts = [2, 2, 4]
        for row, count in enumerate(copy_counts):
            window._dimensions_table.cellWidget(
                row,
                len(BOX_NAMES) + 2,
            ).setValue(count)

        tiles = window._live_preview_widget._preview[2]
        self.assertEqual([tile[5] for tile in tiles], [1, 1, 2, 2, 3, 3, 3, 3])
        self.assertIn("p1 x2, p2 x2, p3 x4", window._live_preview_details.text())
        self.assertIn("8 of 8 slots used", window._output_size_label.text())
        self.assertTrue(window._generate_button.isEnabled() is False)

        window._repeat_columns_control.setValue(3)
        self.assertIn("8 grid slots", window._output_size_label.text())
        self.assertIn("grid has 6", window._output_size_label.text())
        self.assertFalse(window._generate_button.isEnabled())

    def test_open_preview_window_tracks_live_preview_and_fullscreen(self) -> None:
        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        window = MainWindow()
        window._pages = [PageDimensions(page_number=1, boxes=boxes)]
        window._populate_dimensions_table()
        window._refresh_summary()
        window._open_preview_window_button.click()

        preview = window._expanded_preview_window
        self.assertIsInstance(preview, ExpandedPreviewWindow)
        self.assertEqual(preview._preview_widget._preview, window._live_preview_widget._preview)
        preview._toggle_fullscreen()
        self.assertTrue(preview.isFullScreen())
        preview._toggle_fullscreen()
        self.assertFalse(preview.isFullScreen())
        preview.close()

    def test_main_window_summary_reports_document_layout_count(self) -> None:
        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        window = MainWindow()
        window._pages = [
            PageDimensions(page_number=index + 1, boxes=boxes)
            for index in range(8)
        ]
        window._populate_dimensions_table()
        window._repeat_columns_control.setValue(2)
        window._repeat_rows_control.setValue(2)
        window._arrange_pages_checkbox.setChecked(True)

        self.assertIn("2 output layout(s)", window._output_size_label.text())
        self.assertIn("unused slots blank", window._output_size_label.text())
        self.assertIn("Generate layouts", window._generate_button.text())

    def test_source_field_accepts_only_a_single_local_pdf_drop(self) -> None:
        source_field = PdfDropLineEdit()
        dropped_paths: list[str] = []
        source_field.pdfDropped.connect(dropped_paths.append)
        pdf_mime_data = QMimeData()
        pdf_mime_data.setUrls([QUrl.fromLocalFile(r"C:\input.pdf")])
        drag_enter = QDragEnterEvent(
            QPoint(1, 1),
            Qt.DropAction.CopyAction,
            pdf_mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        source_field.dragEnterEvent(drag_enter)
        self.assertTrue(drag_enter.isAccepted())
        drop = QDropEvent(
            QPointF(1, 1),
            Qt.DropAction.CopyAction,
            pdf_mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        source_field.dropEvent(drop)

        self.assertEqual(dropped_paths, ["C:/input.pdf"])
        self.assertTrue(drop.isAccepted())

        invalid_mime_data = QMimeData()
        invalid_mime_data.setUrls([QUrl.fromLocalFile(r"C:\input.txt")])
        invalid_drag = QDragEnterEvent(
            QPoint(1, 1),
            Qt.DropAction.CopyAction,
            invalid_mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        source_field.dragEnterEvent(invalid_drag)
        self.assertFalse(invalid_drag.isAccepted())

    def test_main_window_accepts_pdf_drop_anywhere(self) -> None:
        window = MainWindow()
        self.assertTrue(window.acceptDrops())
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(r"C:\input.pdf")])
        drag_enter = QDragEnterEvent(
            QPoint(1, 1),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        window.dragEnterEvent(drag_enter)
        self.assertTrue(drag_enter.isAccepted())
        drop = QDropEvent(
            QPointF(1, 1),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        with patch.object(window, "_load_source_path") as load_source:
            window.dropEvent(drop)

        load_source.assert_called_once_with("C:/input.pdf")
        self.assertTrue(drop.isAccepted())


if __name__ == "__main__":
    unittest.main()
