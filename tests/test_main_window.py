import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QMimeData, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QComboBox

from pdfdots.main_window import (
    MainWindow,
    OutputPreviewDialog,
    PdfDropLineEdit,
    RepeatLayoutDialog,
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

        window._page_by_page_radio.setChecked(True)
        selection = window._dimensions_table.cellWidget(0, len(BOX_NAMES) + 1)
        self.assertIsInstance(selection, QComboBox)
        self.assertTrue(selection.isEnabled())
        self.assertTrue(window._preview_button.isEnabled())
        self.assertTrue(window._source_field.acceptDrops())

    def test_preview_dialog_shows_geometry_for_selected_page(self) -> None:
        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        page = PageDimensions(page_number=1, boxes=boxes)

        dialog = OutputPreviewDialog(
            [(page, "CropBox")],
            "left_right",
            1.0,
            True,
            2.5,
        )

        self.assertIn("239.00 x 304.00 mm", dialog._details_label.text())
        self.assertIn("210.00 x 297.00 mm", dialog._details_label.text())
        image = QImage(640, 400, QImage.Format.Format_ARGB32)
        dialog._preview_widget.resize(640, 400)
        dialog._preview_widget.render(image)

    def test_repeat_layout_defaults_and_preview_show_outer_dots(self) -> None:
        settings_dialog = RepeatLayoutDialog()
        self.assertEqual(settings_dialog.settings, (1, 1, 5.0, False))
        settings_dialog._columns.setValue(2)
        settings_dialog._rows.setValue(2)
        settings_dialog._arrange_pages.setChecked(True)
        self.assertEqual(settings_dialog.settings, (2, 2, 5.0, True))

        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        page = PageDimensions(page_number=1, boxes=boxes)
        preview = OutputPreviewDialog(
            [(page, "CropBox")],
            "left_right",
            1.0,
            False,
            2.5,
            repeat_columns=2,
            repeat_rows=2,
            repeat_gap_mm=5.0,
        )

        self.assertIn("459.00 x 611.00 mm", preview._details_label.text())
        self.assertIn("dots only outside", preview._details_label.text())
        image = QImage(640, 400, QImage.Format.Format_ARGB32)
        preview._preview_widget.resize(640, 400)
        preview._preview_widget.render(image)

    def test_document_layout_preview_groups_source_pages(self) -> None:
        boxes = {
            name: BoxDimensions(name, 0, 0, 215 * 72 / 25.4, 302 * 72 / 25.4)
            for name in BOX_NAMES
        }
        pages = [
            (PageDimensions(page_number=index + 1, boxes=boxes), "CropBox")
            for index in range(5)
        ]
        preview = OutputPreviewDialog(
            pages,
            "left_right",
            1.0,
            False,
            2.5,
            repeat_columns=2,
            repeat_rows=2,
            arrange_document_pages=True,
        )

        self.assertEqual(len(preview._document_layouts), 2)
        self.assertIn("pages 1–4", preview._page_selector.itemText(0))
        self.assertIn("pages 5–5", preview._page_selector.itemText(1))
        preview._page_selector.setCurrentIndex(1)
        self.assertIn("unused slots blank", preview._details_label.text())
        image = QImage(640, 400, QImage.Format.Format_ARGB32)
        preview._preview_widget.resize(640, 400)
        preview._preview_widget.render(image)

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
        window._repeat_columns = 2
        window._repeat_rows = 2
        window._arrange_document_pages = True
        window._refresh_summary()

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
