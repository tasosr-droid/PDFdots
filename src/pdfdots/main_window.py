from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pikepdf
from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QPointF,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPen,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdfdots.pdf_inspection import (
    BOX_NAMES,
    BoxDimensions,
    DEFAULT_REPEAT_GAP_MM,
    DocumentLayoutGeometry,
    OutputPageGeometry,
    PageDimensions,
    cutter_rectangle_size,
    document_layout_geometries,
    expanded_bleed_box,
    inspect_pdf,
    repeated_document_layout_geometry,
    repeated_output_geometry,
    true_bleed_gap_mm,
)
from pdfdots.pdf_generation import generate_pdf_pair, output_file_paths
from pdfdots.resources import resource_path


def _dropped_pdf_path(event: QDragEnterEvent | QDropEvent) -> str | None:
    urls = event.mimeData().urls()
    if len(urls) != 1 or not urls[0].isLocalFile():
        return None
    path = urls[0].toLocalFile()
    return path if Path(path).suffix.lower() == ".pdf" else None


class PdfDropLineEdit(QLineEdit):
    pdfDropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _dropped_pdf_path(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        path = _dropped_pdf_path(event)
        if path is None:
            event.ignore()
            return
        self.pdfDropped.emit(path)
        event.acceptProposedAction()


class OutputPreviewWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(480, 340)
        self._preview: (
            tuple[
                OutputPageGeometry,
                str,
                list[tuple[object, ...]],
                float,
                float,
                bool,
            ]
            | None
        ) = None

    def set_preview(
        self,
        box: BoxDimensions,
        geometry: OutputPageGeometry,
        layout: str,
        cut_width_mm: float,
        cut_height_mm: float,
        repeat_columns: int = 1,
        repeat_rows: int = 1,
        repeat_gap_mm: float = DEFAULT_REPEAT_GAP_MM,
        tiles: list[tuple[object, ...]] | None = None,
        layout_extent_mm: tuple[float, float] | None = None,
        show_page_numbers: bool = False,
    ) -> None:
        if tiles is None:
            tiles = []
            for row in range(repeat_rows):
                for column in range(repeat_columns):
                    tile_x = column * (box.width_mm + repeat_gap_mm)
                    tile_y = row * (box.height_mm + repeat_gap_mm)
                    tiles.append(
                        (
                            box,
                            tile_x,
                            tile_y,
                            cut_width_mm,
                            cut_height_mm,
                            None,
                            None,
                        )
                    )
        if layout_extent_mm is None:
            layout_extent_mm = (
                repeat_columns * box.width_mm
                + (repeat_columns - 1) * repeat_gap_mm,
                repeat_rows * box.height_mm
                + (repeat_rows - 1) * repeat_gap_mm,
            )
        self._preview = (
            geometry,
            layout,
            tiles,
            layout_extent_mm[0],
            layout_extent_mm[1],
            show_page_numbers,
        )
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f3f5f8"))
        if self._preview is None:
            return

        (
            geometry,
            layout,
            tiles,
            layout_width,
            layout_height,
            show_page_numbers,
        ) = self._preview
        output_width = geometry.width_mm
        output_height = geometry.height_mm
        margin = 32.0
        scale = min(
            (self.width() - 2 * margin) / output_width,
            (self.height() - 2 * margin) / output_height,
        )
        origin_x = (self.width() - output_width * scale) / 2
        origin_y = (self.height() - output_height * scale) / 2

        def point(x_mm: float, y_mm: float) -> QPointF:
            return QPointF(
                origin_x + x_mm * scale,
                origin_y + (output_height - y_mm) * scale,
            )

        painter.setPen(QPen(QColor("#526174"), 1.5))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(
            origin_x,
            origin_y,
            output_width * scale,
            output_height * scale,
        )

        source_x = geometry.source_offset_x_mm
        source_y = geometry.source_offset_y_mm
        painter.setPen(QPen(QColor("#526174"), 1.0))
        painter.setBrush(QColor("#dce6f1"))
        for tile in tiles:
            tile_box = tile[0]
            tile_x, tile_y, cut_width_mm, cut_height_mm = tile[1:5]
            page_number = tile[5] if len(tile) > 5 else None
            page_image = tile[6] if len(tile) > 6 else None
            if not isinstance(tile_box, BoxDimensions):
                continue
            tile_x += source_x
            tile_y += source_y
            source_top_left = point(tile_x, tile_y + tile_box.height_mm)
            source_rect = QRectF(
                source_top_left.x(),
                source_top_left.y(),
                tile_box.width_mm * scale,
                tile_box.height_mm * scale,
            )
            if isinstance(page_image, QImage) and not page_image.isNull():
                image = page_image.scaled(
                    source_rect.size().toSize(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                image_x = source_rect.center().x() - image.width() / 2
                image_y = source_rect.center().y() - image.height() / 2
                painter.drawImage(QPointF(image_x, image_y), image)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.setBrush(QColor("#dce6f1"))
            painter.setPen(QPen(QColor("#526174"), 1.0))
            painter.drawRect(
                source_top_left.x(),
                source_top_left.y(),
                tile_box.width_mm * scale,
                tile_box.height_mm * scale,
            )
            if show_page_numbers and page_number is not None:
                page_number = int(page_number)
                label_width = min(source_rect.width() * 0.7, 100.0)
                label_height = min(source_rect.height() * 0.55, 76.0)
                number_rect = QRectF(
                    source_rect.center().x() - label_width / 2,
                    source_rect.center().y() - label_height / 2,
                    label_width,
                    label_height,
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(20, 25, 32, 190))
                painter.drawRoundedRect(number_rect, 8, 8)
                font = QFont()
                font.setBold(True)
                font.setPixelSize(max(14, min(60, int(label_height * 0.72))))
                painter.setFont(font)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(
                    number_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    str(page_number),
                )

            cut_x = tile_x + (tile_box.width_mm - cut_width_mm) / 2
            cut_y = tile_y + (tile_box.height_mm - cut_height_mm) / 2
            cut_top_left = point(cut_x, cut_y + cut_height_mm)
            painter.setPen(QPen(QColor("#d34b32"), 2.0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(
                cut_top_left.x(),
                cut_top_left.y(),
                cut_width_mm * scale,
                cut_height_mm * scale,
            )
            painter.setPen(QPen(QColor("#526174"), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)

        if layout == "left_right":
            centers = (
                (source_x - 8, source_y + 3),
                (source_x + layout_width + 8, source_y + 3),
                (
                    source_x + layout_width + 8,
                    source_y + layout_height - 3,
                ),
                (source_x - 8, source_y + layout_height - 3),
            )
        else:
            centers = (
                (source_x + 3, source_y - 8),
                (source_x + 3, source_y + layout_height + 8),
                (
                    source_x + layout_width - 3,
                    source_y + layout_height + 8,
                ),
                (source_x + layout_width - 3, source_y - 8),
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#17191c"))
        dot_radius = 3 * scale
        for center_x, center_y in centers:
            center = point(center_x, center_y)
            painter.drawEllipse(center, dot_radius, dot_radius)
        painter.end()


    def clear_preview(self) -> None:
        self._preview = None
        self.update()

    def copy_preview_to(self, other: "OutputPreviewWidget") -> None:
        other._preview = self._preview
        other.update()


class ExpandedPreviewWindow(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF Dots — output preview")
        self.resize(1200, 850)
        self._windowed_geometry = self.geometry()
        self._preview_widget = OutputPreviewWidget()
        self._preview_widget.setMinimumSize(640, 480)
        self._details_label = QLabel()
        self._details_label.setWordWrap(True)
        self._fullscreen_button = QPushButton("Full screen")
        self._fullscreen_button.clicked.connect(self._toggle_fullscreen)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        controls = QHBoxLayout()
        controls.addStretch()
        controls.addWidget(self._fullscreen_button)
        controls.addWidget(close_button)
        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self._preview_widget, stretch=1)
        layout.addWidget(self._details_label)

    def update_preview(
        self,
        preview_widget: OutputPreviewWidget,
        details: str,
    ) -> None:
        preview_widget.copy_preview_to(self._preview_widget)
        self._details_label.setText(details)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.setGeometry(self._windowed_geometry)
            self._fullscreen_button.setText("Full screen")
        else:
            self._windowed_geometry = self.geometry()
            self.showFullScreen()
            self._fullscreen_button.setText("Exit full screen")


class OutputPreviewDialog(QDialog):
    def __init__(
        self,
        selected_pages: list[tuple[PageDimensions, str]],
        layout: str,
        safety_distance_mm: float,
        fake_bleed_enabled: bool,
        fake_bleed_inset_mm: float,
        parent: QWidget | None = None,
        repeat_columns: int = 1,
        repeat_rows: int = 1,
        repeat_gap_mm: float = DEFAULT_REPEAT_GAP_MM,
        arrange_document_pages: bool = False,
        combine_document_pages: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Output layout preview")
        self.resize(760, 560)
        self._selected_pages = selected_pages
        self._layout = layout
        self._safety_distance_mm = safety_distance_mm
        self._fake_bleed_enabled = fake_bleed_enabled
        self._fake_bleed_inset_mm = fake_bleed_inset_mm
        self._repeat_columns = repeat_columns
        self._repeat_rows = repeat_rows
        self._repeat_gap_mm = repeat_gap_mm
        self._arrange_document_pages = arrange_document_pages
        self._combine_document_pages = combine_document_pages
        selected_boxes = [
            page.boxes[box_name] for page, box_name in selected_pages
        ]
        self._document_layouts: list[DocumentLayoutGeometry] = (
            [
                repeated_document_layout_geometry(
                    selected_boxes,
                    [1] * len(selected_boxes),
                    layout,
                    safety_distance_mm,
                    repeat_columns,
                    repeat_rows,
                    repeat_gap_mm,
                )
            ]
            if combine_document_pages
            else document_layout_geometries(
                selected_boxes,
                layout,
                safety_distance_mm,
                repeat_columns,
                repeat_rows,
                repeat_gap_mm,
            )
            if arrange_document_pages
            else []
        )

        layout_widget = QVBoxLayout(self)
        page_row = QHBoxLayout()
        page_label = QLabel("Preview page:")
        page_row.addWidget(page_label)
        self._page_selector = QComboBox()
        if arrange_document_pages or combine_document_pages:
            capacity = repeat_columns * repeat_rows
            for layout_index, document_layout in enumerate(
                self._document_layouts,
                start=1,
            ):
                page_numbers = [
                    selected_pages[placement.page_index][0].page_number
                    for placement in document_layout.placements
                ]
                page_description = (
                    f"{capacity} copies per page"
                    if combine_document_pages
                    else f"{capacity} slots"
                )
                self._page_selector.addItem(
                    f"Layout {layout_index} — pages "
                    f"{page_numbers[0]}–{page_numbers[-1]} "
                    f"({page_description})",
                    layout_index - 1,
                )
        else:
            for page, box_name in selected_pages:
                self._page_selector.addItem(
                    f"Page {page.page_number} — {box_name}",
                    page.page_number,
                )
        page_row.addWidget(self._page_selector)
        page_row.addStretch()
        selector_visible = (
            len(self._document_layouts) > 1
            if arrange_document_pages or combine_document_pages
            else len(selected_pages) > 1
        )
        self._page_selector.setVisible(selector_visible)
        page_label.setVisible(selector_visible)
        layout_widget.addLayout(page_row)

        self._preview_widget = OutputPreviewWidget()
        layout_widget.addWidget(self._preview_widget, stretch=1)
        self._details_label = QLabel()
        self._details_label.setWordWrap(True)
        layout_widget.addWidget(self._details_label)
        layout_widget.addWidget(
            QLabel(
                "Legend: shaded area = selected source box; dashed outline = "
                "cutter rectangle; black circles = registration dots. "
                "Schematic only; source artwork is not rendered."
            )
        )
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_button)
        layout_widget.addLayout(close_row)
        self._page_selector.currentIndexChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self, *_args: object) -> None:
        if not self._selected_pages:
            return
        index = self._page_selector.currentIndex()
        if self._arrange_document_pages or self._combine_document_pages:
            document_layout = self._document_layouts[index]
            tiles = []
            for placement in document_layout.placements:
                page, box_name = self._selected_pages[placement.page_index]
                box = page.boxes[box_name]
                cut_width, cut_height = (
                    cutter_rectangle_size(box, self._fake_bleed_inset_mm)
                    if self._fake_bleed_enabled
                    else (box.width_mm, box.height_mm)
                )
                tiles.append(
                    (
                        box,
                        placement.offset_x_mm,
                        placement.offset_y_mm,
                        cut_width,
                        cut_height,
                    )
                )
            first_page = self._selected_pages[
                document_layout.placements[0].page_index
            ][0].page_number
            last_page = self._selected_pages[
                document_layout.placements[-1].page_index
            ][0].page_number
            first_box = tiles[0][0]
            geometry = OutputPageGeometry(
                document_layout.width_mm,
                document_layout.height_mm,
                document_layout.source_offset_x_mm,
                document_layout.source_offset_y_mm,
            )
            self._preview_widget.set_preview(
                first_box,
                geometry,
                self._layout,
                tiles[0][3],
                tiles[0][4],
                tiles=tiles,
                layout_extent_mm=(
                    document_layout.grid_width_mm,
                    document_layout.grid_height_mm,
                ),
            )
            layout_note = (
                "each page repeated; page grids stacked"
                if self._combine_document_pages
                else "unused slots blank"
            )
            self._details_label.setText(
                f"Output: {geometry.width_mm:.2f} x {geometry.height_mm:.2f} mm  |  "
                f"Layout {index + 1}: source pages {first_page}–{last_page}  |  "
                f"{self._repeat_columns} x {self._repeat_rows}, "
                f"{self._repeat_gap_mm:.2f} mm gap; {layout_note}; "
                "dots only outside"
            )
            return

        page, box_name = self._selected_pages[index]
        box = page.boxes[box_name]
        geometry = repeated_output_geometry(
            box,
            self._layout,
            self._safety_distance_mm,
            self._repeat_columns,
            self._repeat_rows,
            self._repeat_gap_mm,
        )
        cut_width, cut_height = (
            cutter_rectangle_size(box, self._fake_bleed_inset_mm)
            if self._fake_bleed_enabled
            else (box.width_mm, box.height_mm)
        )
        self._preview_widget.set_preview(
            box,
            geometry,
            self._layout,
            cut_width,
            cut_height,
            self._repeat_columns,
            self._repeat_rows,
            self._repeat_gap_mm,
        )
        self._details_label.setText(
            f"Output: {geometry.width_mm:.2f} x {geometry.height_mm:.2f} mm  |  "
            f"Source: {box.format_mm()}  |  "
            f"Cutter rectangle: {cut_width:.2f} x {cut_height:.2f} mm  |  "
            f"Repeat: {self._repeat_columns} x {self._repeat_rows}, "
            f"{self._repeat_gap_mm:.2f} mm gap; dots only outside"
        )


class RepeatLayoutDialog(QDialog):
    def __init__(
        self,
        repeat_columns: int = 1,
        repeat_rows: int = 1,
        repeat_gap_mm: float = DEFAULT_REPEAT_GAP_MM,
        parent: QWidget | None = None,
        arrange_document_pages: bool = False,
        combine_document_pages: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Page layout settings")
        self.setModal(True)

        dialog_layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self._columns = QSpinBox()
        self._columns.setRange(1, 100)
        self._columns.setValue(repeat_columns)
        self._rows = QSpinBox()
        self._rows.setRange(1, 100)
        self._rows.setValue(repeat_rows)
        self._gap = QDoubleSpinBox()
        self._gap.setRange(0.0, 100.0)
        self._gap.setDecimals(2)
        self._gap.setSingleStep(0.5)
        self._gap.setSuffix(" mm")
        self._gap.setValue(repeat_gap_mm)
        self._arrange_pages = QCheckBox(
            "Arrange consecutive document pages in the grid"
        )
        self._arrange_pages.setChecked(arrange_document_pages)
        self._combine_pages = QCheckBox(
            "Put all document pages on one layout and repeat each page"
        )
        self._combine_pages.setChecked(combine_document_pages)
        self._arrange_pages.toggled.connect(
            lambda checked: self._combine_pages.setChecked(False)
            if checked
            else None
        )
        self._combine_pages.toggled.connect(
            lambda checked: self._arrange_pages.setChecked(False)
            if checked
            else None
        )

        controls.addWidget(QLabel("Columns:"))
        controls.addWidget(self._columns)
        controls.addWidget(QLabel("Rows:"))
        controls.addWidget(self._rows)
        controls.addWidget(QLabel("Gap:"))
        controls.addWidget(self._gap)
        dialog_layout.addLayout(controls)
        dialog_layout.addWidget(
            QLabel(
                "When off, each source page repeats across its own layout. "
                "Arrange mode places consecutive pages once per grid slot. "
                "Single-layout mode stacks a repeat grid for every page "
                "vertically on one sheet."
            )
        )
        dialog_layout.addWidget(self._arrange_pages)
        dialog_layout.addWidget(self._combine_pages)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        dialog_layout.addWidget(buttons)

    @property
    def settings(self) -> tuple[int, int, float, bool, bool]:
        return (
            self._columns.value(),
            self._rows.value(),
            self._gap.value(),
            self._arrange_pages.isChecked(),
            self._combine_pages.isChecked(),
        )


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setWindowTitle("PDF Dots v0.5 by Tasos Rizopoulos 2026")
        self.setWindowIcon(QIcon(str(resource_path("dots.png"))))
        self.setMinimumSize(1060, 680)

        self._source_path: Path | None = None
        self._output_directory: Path | None = None
        self._pages: list[PageDimensions] = []
        self._pdf_preview_document: QPdfDocument | None = None
        self._preview_pdf_buffer: QBuffer | None = None
        self._preview_image_cache: dict[int, QImage] = {}
        self._preview_error: str | None = None
        self._repeat_columns = 1
        self._repeat_rows = 1
        self._repeat_gap_mm = DEFAULT_REPEAT_GAP_MM
        self._arrange_document_pages = False
        self._repeat_pages_individually = False

        self._source_field = PdfDropLineEdit()
        self._source_field.setReadOnly(True)
        self._source_field.setPlaceholderText("Choose a PDF or drag one here")
        self._source_field.pdfDropped.connect(self._load_source_path)
        self._output_field = QLineEdit()
        self._output_field.setReadOnly(True)
        self._output_field.setPlaceholderText("Choose an output folder")

        source_group = self._create_path_group(
            "Source PDF", self._source_field, self._choose_source
        )
        output_group = self._create_path_group(
            "Output folder", self._output_field, self._choose_output_directory
        )

        self._job_description = QLineEdit()
        self._job_description.setPlaceholderText("Optional job description")
        job_group = QGroupBox("Job description for output filenames")
        job_layout = QHBoxLayout(job_group)
        job_layout.addWidget(self._job_description)

        self._whole_document_radio = QRadioButton("Use one source box for the document")
        self._page_by_page_radio = QRadioButton("Choose a source box page by page")
        self._whole_document_radio.setChecked(True)
        self._scope_group = QButtonGroup(self)
        self._scope_group.addButton(self._whole_document_radio)
        self._scope_group.addButton(self._page_by_page_radio)
        self._whole_document_radio.toggled.connect(self._update_box_selection_mode)

        source_size_group = QGroupBox("Source box to use")
        source_size_layout = QHBoxLayout(source_size_group)
        self._box_group = QButtonGroup(self)
        self._box_radios: dict[str, QRadioButton] = {}
        for box_name in BOX_NAMES:
            radio = QRadioButton(box_name)
            radio.setChecked(box_name == "CropBox")
            self._box_group.addButton(radio)
            self._box_radios[box_name] = radio
            source_size_layout.addWidget(radio)
            radio.toggled.connect(self._refresh_summary)

        self._dimensions_table = QTableWidget(0, len(BOX_NAMES) + 3)
        self._dimensions_table.setHorizontalHeaderLabels(
            ["Page", *BOX_NAMES, "Selected source box", "Copies"]
        )
        self._dimensions_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._dimensions_table.setAlternatingRowColors(True)
        self._dimensions_table.horizontalHeader().setStretchLastSection(True)

        self._dimensions_group = QGroupBox("PDF page dimensions")
        dimensions_layout = QVBoxLayout(self._dimensions_group)
        dimensions_layout.addWidget(
            QLabel(
                "Dimensions are shown in millimetres. CropBox is selected by "
                "default; each value is the page's effective PDF box."
            )
        )
        dimensions_layout.addWidget(self._dimensions_table, stretch=1)

        self._safety_distance = self._make_mm_control(1.0)
        safety_group = QGroupBox("Safety distance")
        safety_layout = QHBoxLayout(safety_group)
        safety_layout.addWidget(QLabel("Extra clearance around registration marks:"))
        safety_layout.addWidget(self._safety_distance)
        safety_layout.addWidget(QLabel("mm"))
        safety_layout.addStretch()
        self._safety_distance.valueChanged.connect(self._refresh_summary)

        self._left_right_layout = QRadioButton("Dots on left and right")
        self._top_bottom_layout = QRadioButton("Dots on top and bottom")
        self._left_right_layout.setChecked(True)
        self._layout_group = QButtonGroup(self)
        self._layout_group.addButton(self._left_right_layout)
        self._layout_group.addButton(self._top_bottom_layout)
        layout_group = QGroupBox("Registration-dot layout")
        layout_choice_layout = QHBoxLayout(layout_group)
        layout_choice_layout.addWidget(self._left_right_layout)
        layout_choice_layout.addWidget(self._top_bottom_layout)
        self._left_right_layout.toggled.connect(self._refresh_summary)

        self._fake_bleed_checkbox = QCheckBox("Apply fake bleed to cutter rectangle")
        self._fake_bleed_inset = self._make_mm_control(2.5)
        self._fake_bleed_inset.setEnabled(False)
        self._fake_bleed_checkbox.toggled.connect(self._fake_bleed_inset.setEnabled)
        self._fake_bleed_checkbox.toggled.connect(self._refresh_summary)
        self._fake_bleed_inset.valueChanged.connect(self._refresh_summary)
        fake_bleed_layout = QHBoxLayout()
        fake_bleed_layout.addWidget(self._fake_bleed_checkbox)
        fake_bleed_layout.addWidget(QLabel("Inset on each side:"))
        fake_bleed_layout.addWidget(self._fake_bleed_inset)
        fake_bleed_layout.addWidget(QLabel("mm"))
        fake_bleed_layout.addStretch()

        self._true_bleed_checkbox = QCheckBox("True bleed")
        self._true_bleed_checkbox.setEnabled(False)
        self._true_bleed_amount = self._make_mm_control(2.0)
        self._true_bleed_amount.setEnabled(False)
        self._true_bleed_checkbox.toggled.connect(self._refresh_summary)
        self._true_bleed_amount.valueChanged.connect(self._refresh_summary)
        true_bleed_layout = QHBoxLayout()
        true_bleed_layout.addWidget(self._true_bleed_checkbox)
        true_bleed_layout.addWidget(QLabel("Bleed on each side:"))
        true_bleed_layout.addWidget(self._true_bleed_amount)
        true_bleed_layout.addWidget(QLabel("mm"))
        true_bleed_layout.addStretch()

        bleed_group = QGroupBox("Bleed and cutter options")
        bleed_layout = QVBoxLayout(bleed_group)
        bleed_layout.addLayout(fake_bleed_layout)
        bleed_layout.addLayout(true_bleed_layout)

        self._repeat_columns_control = QSpinBox()
        self._repeat_columns_control.setRange(1, 100)
        self._repeat_columns_control.setValue(self._repeat_columns)
        self._repeat_rows_control = QSpinBox()
        self._repeat_rows_control.setRange(1, 100)
        self._repeat_rows_control.setValue(self._repeat_rows)
        self._repeat_gap_control = QDoubleSpinBox()
        self._repeat_gap_control.setRange(0.0, 100.0)
        self._repeat_gap_control.setDecimals(2)
        self._repeat_gap_control.setSingleStep(0.5)
        self._repeat_gap_control.setSuffix(" mm")
        self._repeat_gap_control.setValue(self._repeat_gap_mm)
        repeat_controls = QHBoxLayout()
        repeat_controls.addWidget(QLabel("Columns:"))
        repeat_controls.addWidget(self._repeat_columns_control)
        repeat_controls.addWidget(QLabel("Rows:"))
        repeat_controls.addWidget(self._repeat_rows_control)
        repeat_controls.addWidget(QLabel("Gap:"))
        repeat_controls.addWidget(self._repeat_gap_control)

        self._arrange_pages_checkbox = QCheckBox(
            "Arrange consecutive pages into grid layouts"
        )
        self._repeat_pages_checkbox = QCheckBox(
            "Place document pages in one grid; use per-page copy counts"
        )
        self._arrange_pages_checkbox.toggled.connect(
            self._set_arrange_document_pages
        )
        self._repeat_pages_checkbox.toggled.connect(
            self._set_repeat_pages_individually
        )
        self._repeat_columns_control.valueChanged.connect(
            self._repeat_settings_changed
        )
        self._repeat_rows_control.valueChanged.connect(
            self._repeat_settings_changed
        )
        self._repeat_gap_control.valueChanged.connect(
            self._repeat_settings_changed
        )
        repeat_group = QGroupBox("Repeat and multipage layout")
        repeat_layout = QVBoxLayout(repeat_group)
        repeat_layout.addLayout(repeat_controls)
        repeat_layout.addWidget(self._arrange_pages_checkbox)
        repeat_layout.addWidget(self._repeat_pages_checkbox)

        self._summary_label = QLabel(
            "Load a PDF to inspect its page boxes and resulting cut size."
        )
        self._summary_label.setWordWrap(True)
        self._output_size_label = QLabel(
            "Expanded output size will be shown here after loading a PDF."
        )
        self._output_size_label.setWordWrap(True)
        self._generate_button = QPushButton("Generate files of size —")
        self._generate_button.setEnabled(False)
        self._generate_button.clicked.connect(self._generate_files)
        self._reset_settings_button = QPushButton("Reset settings")
        self._reset_settings_button.clicked.connect(self._reset_settings)

        self._actual_preview_checkbox = QCheckBox("Render actual PDF pages")
        self._actual_preview_checkbox.setEnabled(False)
        self._actual_preview_checkbox.toggled.connect(self._refresh_live_preview)
        self._expanded_preview_window: ExpandedPreviewWindow | None = None
        self._open_preview_window_button = QPushButton("Open larger preview")
        self._open_preview_window_button.clicked.connect(
            self._open_expanded_preview
        )
        self._live_preview_selector = QComboBox()
        self._live_preview_selector.currentIndexChanged.connect(
            self._refresh_live_preview
        )
        self._live_preview_widget = OutputPreviewWidget()
        self._live_preview_widget.setMinimumSize(360, 240)
        self._live_preview_details = QLabel(
            "Load a PDF to see the output layout."
        )
        self._live_preview_details.setWordWrap(True)
        live_preview_group = QGroupBox("Live layout preview")
        live_preview_layout = QVBoxLayout(live_preview_group)
        live_preview_layout.addWidget(self._open_preview_window_button)
        live_preview_layout.addWidget(self._live_preview_selector)
        live_preview_layout.addWidget(self._actual_preview_checkbox)
        live_preview_layout.addWidget(self._live_preview_widget, stretch=1)
        live_preview_layout.addWidget(self._live_preview_details)
        live_preview_layout.addWidget(
            QLabel(
                "Shaded = source page; dashed red = cutter rectangle; "
                "black = registration dots. Page numbers identify source pages."
            )
        )

        central_widget = QWidget()
        root_layout = QHBoxLayout(central_widget)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(source_group)
        left_layout.addWidget(job_group)
        left_layout.addWidget(output_group)
        scope_layout = QHBoxLayout()
        scope_layout.addWidget(self._whole_document_radio)
        scope_layout.addWidget(self._page_by_page_radio)
        scope_layout.addStretch()
        left_layout.addLayout(scope_layout)
        left_layout.addWidget(source_size_group)
        left_layout.addWidget(safety_group)
        left_layout.addWidget(layout_group)
        left_layout.addWidget(bleed_group)
        left_layout.addWidget(repeat_group)
        left_layout.addWidget(self._summary_label)
        left_layout.addWidget(self._output_size_label)
        button_row = QHBoxLayout()
        button_row.addWidget(self._reset_settings_button)
        button_row.addWidget(self._generate_button, stretch=1)
        left_layout.addLayout(button_row)
        left_layout.addStretch()

        left_scroll_area = QScrollArea()
        left_scroll_area.setWidgetResizable(True)
        left_scroll_area.setWidget(left_panel)
        root_layout.addWidget(left_scroll_area, stretch=1)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(self._dimensions_group, stretch=2)
        right_layout.addWidget(live_preview_group, stretch=3)
        root_layout.addWidget(right_panel, stretch=1)
        self.setCentralWidget(central_widget)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _dropped_pdf_path(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        path = _dropped_pdf_path(event)
        if path is None:
            event.ignore()
            return
        self._load_source_path(path)
        event.acceptProposedAction()

    @staticmethod
    def _make_mm_control(value: float) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setRange(0.0, 100.0)
        control.setDecimals(2)
        control.setSingleStep(0.5)
        control.setValue(value)
        return control

    @staticmethod
    def _create_path_group(
        title: str,
        field: QLineEdit,
        browse_callback: Callable[[], None],
    ) -> QGroupBox:
        group = QGroupBox(title)
        layout = QHBoxLayout(group)
        layout.addWidget(field, stretch=1)
        button = QPushButton("Browse...")
        button.clicked.connect(browse_callback)
        layout.addWidget(button)
        return group

    def _choose_source(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose source PDF",
            "",
            "PDF files (*.pdf)",
        )
        if not file_path:
            return

        self._load_source_path(file_path)

    def _load_source_path(self, file_path: str) -> None:
        path = Path(file_path)
        try:
            pages = inspect_pdf(path)
        except (OSError, pikepdf.PdfError, ValueError) as error:
            QMessageBox.critical(self, "Could not read PDF", str(error))
            return

        self._close_pdf_preview()
        preview_buffer: QBuffer | None = None
        preview_document: QPdfDocument | None = None
        try:
            with pikepdf.Pdf.open(path) as preview_pdf:
                for page in preview_pdf.pages:
                    page.obj["/CropBox"] = pikepdf.Array(page.mediabox)
                preview_pdf_bytes = BytesIO()
                preview_pdf.save(preview_pdf_bytes)

            preview_buffer = QBuffer(self)
            preview_buffer.setData(preview_pdf_bytes.getvalue())
            if not preview_buffer.open(QIODevice.OpenModeFlag.ReadOnly):
                raise OSError("Could not open the in-memory PDF preview.")

            preview_document = QPdfDocument(self)
            preview_document.load(preview_buffer)
            if (
                preview_document.status() != QPdfDocument.Status.Ready
                or preview_document.pageCount() != len(pages)
            ):
                raise ValueError("Could not render the PDF preview.")

            self._pdf_preview_document = preview_document
            self._preview_pdf_buffer = preview_buffer
            self._preview_error = None
        except (OSError, pikepdf.PdfError, ValueError) as error:
            if preview_document is not None:
                preview_document.close()
                preview_document.deleteLater()
            if preview_buffer is not None:
                preview_buffer.close()
                preview_buffer.deleteLater()
            self._pdf_preview_document = None
            self._preview_pdf_buffer = None
            self._preview_error = f"Could not load PDF preview: {error}"
            QMessageBox.warning(
                self,
                "Page preview unavailable",
                self._preview_error,
            )
        self._preview_image_cache.clear()
        self._source_path = path
        self._pages = pages
        self._source_field.setText(str(path))
        preview_available = self._pdf_preview_document is not None
        self._actual_preview_checkbox.setEnabled(preview_available)
        self._actual_preview_checkbox.setChecked(preview_available)
        self._populate_dimensions_table()
        self._update_box_selection_mode()
        self._refresh_summary()

    def _close_pdf_preview(self) -> None:
        if self._pdf_preview_document is not None:
            self._pdf_preview_document.close()
            self._pdf_preview_document.deleteLater()
            self._pdf_preview_document = None
        if self._preview_pdf_buffer is not None:
            self._preview_pdf_buffer.close()
            self._preview_pdf_buffer.deleteLater()
            self._preview_pdf_buffer = None

    def _choose_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose output folder",
        )
        if not directory:
            return

        self._output_directory = Path(directory)
        self._output_field.setText(str(self._output_directory))
        self._refresh_summary()

    def _populate_dimensions_table(self) -> None:
        self._dimensions_table.setRowCount(len(self._pages))
        for row, page in enumerate(self._pages):
            page_item = QTableWidgetItem(str(page.page_number))
            page_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._dimensions_table.setItem(row, 0, page_item)

            for column, box_name in enumerate(BOX_NAMES, start=1):
                self._dimensions_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(page.boxes[box_name].format_mm()),
                )

            selection = QComboBox()
            for box_name in BOX_NAMES:
                selection.addItem(box_name, box_name)
            selection.setCurrentIndex(selection.findData("CropBox"))
            selection.currentIndexChanged.connect(self._refresh_summary)
            self._dimensions_table.setCellWidget(row, len(BOX_NAMES) + 1, selection)

            copies = QSpinBox()
            copies.setRange(0, 1000)
            copies.setValue(1)
            copies.valueChanged.connect(self._refresh_summary)
            self._dimensions_table.setCellWidget(row, len(BOX_NAMES) + 2, copies)

        self._dimensions_table.resizeColumnsToContents()

    def _update_box_selection_mode(self) -> None:
        page_by_page = self._page_by_page_radio.isChecked()
        self._box_group.setExclusive(not page_by_page)
        for radio in self._box_radios.values():
            radio.setEnabled(not page_by_page)
        for row in range(self._dimensions_table.rowCount()):
            selection = self._dimensions_table.cellWidget(row, len(BOX_NAMES) + 1)
            if isinstance(selection, QComboBox):
                selection.setEnabled(page_by_page)
            copies = self._dimensions_table.cellWidget(row, len(BOX_NAMES) + 2)
            if isinstance(copies, QSpinBox):
                copies.setEnabled(self._repeat_pages_checkbox.isChecked())
        self._refresh_summary()

    def _selected_boxes(self) -> list[tuple[PageDimensions, str]]:
        if self._page_by_page_radio.isChecked():
            result: list[tuple[PageDimensions, str]] = []
            for row, page in enumerate(self._pages):
                selection = self._dimensions_table.cellWidget(
                    row, len(BOX_NAMES) + 1
                )
                if isinstance(selection, QComboBox):
                    result.append((page, str(selection.currentData())))
            return result

        selected_box = next(
            (name for name, radio in self._box_radios.items() if radio.isChecked()),
            "CropBox",
        )
        return [(page, selected_box) for page in self._pages]

    def _selected_page_copy_counts(self) -> list[int]:
        counts = []
        for row in range(self._dimensions_table.rowCount()):
            control = self._dimensions_table.cellWidget(
                row,
                len(BOX_NAMES) + 2,
            )
            counts.append(control.value() if isinstance(control, QSpinBox) else 1)
        return counts

    def _layout_boxes(
        self,
        selected: list[tuple[PageDimensions, str]],
    ) -> list[BoxDimensions]:
        boxes = [page.boxes[box_name] for page, box_name in selected]
        if not self._true_bleed_checkbox.isChecked():
            return boxes
        bleed_mm = self._true_bleed_amount.value()
        return [
            expanded_bleed_box(box, page.boxes["MediaBox"], bleed_mm)
            for (page, _), box in zip(selected, boxes)
        ]

    def _has_adjacent_pages(self) -> bool:
        return (
            sum(self._selected_page_copy_counts()) > 1
            if self._repeat_pages_individually
            else (
                self._arrange_document_pages
                and len(self._selected_boxes()) > 1
                and self._repeat_columns * self._repeat_rows > 1
            )
            or (
                not self._arrange_document_pages
                and (self._repeat_columns > 1 or self._repeat_rows > 1)
            )
        )

    def _update_true_bleed_availability(self) -> None:
        selected = self._selected_boxes()
        reason: str | None = None
        if not selected:
            reason = "Load a PDF before enabling true bleed."
        else:
            try:
                for page, box_name in selected:
                    expanded_bleed_box(
                        page.boxes[box_name],
                        page.boxes["MediaBox"],
                        self._true_bleed_amount.value(),
                    )
                if self._has_adjacent_pages():
                    true_bleed_gap_mm(
                        self._repeat_gap_mm,
                        self._true_bleed_amount.value(),
                    )
            except ValueError as error:
                reason = str(error)

        available = reason is None
        if not available and self._true_bleed_checkbox.isChecked():
            with QSignalBlocker(self._true_bleed_checkbox):
                self._true_bleed_checkbox.setChecked(False)
        self._true_bleed_checkbox.setEnabled(available)
        self._true_bleed_checkbox.setToolTip(reason or "")
        self._true_bleed_amount.setEnabled(
            bool(self._pages)
            and (
                self._true_bleed_checkbox.isChecked()
                or not available
            )
        )

    def _layout_gap_mm(self) -> float:
        if not self._true_bleed_checkbox.isChecked():
            return self._repeat_gap_mm
        if not self._has_adjacent_pages():
            return self._repeat_gap_mm
        return true_bleed_gap_mm(
            self._repeat_gap_mm,
            self._true_bleed_amount.value(),
        )

    def _reset_settings(self) -> None:
        self._whole_document_radio.setChecked(True)
        self._box_radios["CropBox"].setChecked(True)
        for row in range(self._dimensions_table.rowCount()):
            selection = self._dimensions_table.cellWidget(
                row,
                len(BOX_NAMES) + 1,
            )
            if isinstance(selection, QComboBox):
                selection.setCurrentIndex(selection.findData("CropBox"))
            copies = self._dimensions_table.cellWidget(
                row,
                len(BOX_NAMES) + 2,
            )
            if isinstance(copies, QSpinBox):
                copies.setValue(1)

        self._safety_distance.setValue(1.0)
        self._left_right_layout.setChecked(True)
        self._fake_bleed_checkbox.setChecked(False)
        self._fake_bleed_inset.setValue(2.5)
        self._true_bleed_checkbox.setChecked(False)
        self._true_bleed_amount.setValue(2.0)
        self._repeat_columns_control.setValue(1)
        self._repeat_rows_control.setValue(1)
        self._repeat_gap_control.setValue(DEFAULT_REPEAT_GAP_MM)
        self._arrange_pages_checkbox.setChecked(False)
        self._repeat_pages_checkbox.setChecked(False)
        self._refresh_summary()

    def _refresh_summary(self, *_args: object) -> None:
        self._update_true_bleed_availability()
        if not self._pages:
            return

        selected = self._selected_boxes()
        dimensions = [
            page.boxes[box_name]
            for page, box_name in selected
        ]
        sizes = {(round(box.width_mm, 2), round(box.height_mm, 2)) for box in dimensions}
        if len(sizes) == 1:
            width, height = next(iter(sizes))
            source_summary = f"Selected source size: {width:.2f} x {height:.2f} mm."
        else:
            source_summary = "Selected source size varies by page: "
            source_summary += "; ".join(
                f"page {page.page_number}: {page.boxes[box_name].format_mm()}"
                for page, box_name in selected
            )

        generation_valid = True
        try:
            layout_dimensions = self._layout_boxes(selected)
            layout_gap_mm = self._layout_gap_mm()
        except ValueError as error:
            layout_dimensions = dimensions
            layout_gap_mm = self._repeat_gap_mm
            generation_valid = False
            bleed_summary = str(error)
        else:
            bleed_summary = (
                f"True bleed: {self._true_bleed_amount.value():.2f} mm per side; "
                f"{layout_gap_mm:.2f} mm between printed pages."
                if self._true_bleed_checkbox.isChecked()
                else "True bleed is off."
            )

        if self._fake_bleed_checkbox.isChecked():
            inset = self._fake_bleed_inset.value()
            try:
                cut_sizes = [
                    cutter_rectangle_size(box, inset)
                    for box in dimensions
                ]
            except ValueError:
                generation_valid = False
                cut_summary = (
                    "Fake bleed inset is too large for at least one selected page."
                )
            else:
                unique_cut_sizes = {
                    (round(width, 2), round(height, 2))
                    for width, height in cut_sizes
                }
                if len(unique_cut_sizes) == 1:
                    cut_width, cut_height = next(iter(unique_cut_sizes))
                    cut_summary = (
                        f"Cutter rectangle after {inset:.2f} mm fake bleed "
                        f"per side: {cut_width:.2f} x {cut_height:.2f} mm."
                    )
                else:
                    cut_summary = "Cutter rectangle sizes after fake bleed: "
                    cut_summary += "; ".join(
                        f"page {page.page_number}: "
                        f"{width:.2f} x {height:.2f} mm"
                        for (page, _), (width, height) in zip(selected, cut_sizes)
                    )
        else:
            cut_summary = (
                "Cutter rectangle matches the selected source box; fake bleed "
                "is off."
            )

        layout = "left_right" if self._left_right_layout.isChecked() else "top_bottom"
        layout_description = (
            "left and right" if layout == "left_right" else "top and bottom"
        )
        page_copy_counts = self._selected_page_copy_counts()
        if self._repeat_pages_individually:
            copy_total = sum(page_copy_counts)
            capacity = self._repeat_columns * self._repeat_rows
            if copy_total == 0 or copy_total > capacity:
                output_geometries = []
                generation_valid = False
            else:
                output_geometries = [
                    repeated_document_layout_geometry(
                        layout_dimensions,
                        page_copy_counts,
                        layout,
                        self._safety_distance.value(),
                        self._repeat_columns,
                        self._repeat_rows,
                        layout_gap_mm,
                    )
                ]
        elif self._arrange_document_pages:
            output_geometries = document_layout_geometries(
                layout_dimensions,
                layout,
                self._safety_distance.value(),
                self._repeat_columns,
                self._repeat_rows,
                layout_gap_mm,
            )
        else:
            output_geometries = [
                repeated_output_geometry(
                    box,
                    layout,
                    self._safety_distance.value(),
                    self._repeat_columns,
                    self._repeat_rows,
                    layout_gap_mm,
                )
                for box in layout_dimensions
            ]
        output_sizes = {
            (round(item.width_mm, 2), round(item.height_mm, 2))
            for item in output_geometries
        }
        if self._repeat_pages_individually and not output_geometries:
            copy_total = sum(page_copy_counts)
            capacity = self._repeat_columns * self._repeat_rows
            if copy_total == 0:
                output_summary = "Set at least one page copy to make a layout."
            else:
                output_summary = (
                    f"Page copies require {copy_total} grid slots, but the "
                    f"configured grid has {capacity}. Increase rows or columns "
                    "or lower the per-page copy counts."
                )
            self._generate_button.setText("Page copies do not fit")
        elif len(output_sizes) == 1:
            output_width, output_height = next(iter(output_sizes))
            output_size = f"{output_width:.2f} x {output_height:.2f} mm"
            if self._repeat_pages_individually:
                output_summary = (
                    f"One {self._repeat_columns} x {self._repeat_rows} grid; "
                    f"{sum(page_copy_counts)} of "
                    f"{self._repeat_columns * self._repeat_rows} slots used, "
                    f"{self._repeat_gap_mm:.2f} mm gap: {output_size}."
                )
                self._generate_button.setText(
                    f"Generate single layout of size {output_size}"
                )
            elif self._arrange_document_pages:
                output_summary = (
                    f"{len(output_geometries)} output layout(s), "
                    f"{self._repeat_columns} across x {self._repeat_rows} down "
                    f"({self._repeat_gap_mm:.2f} mm gap; unused slots blank): "
                    f"{output_size}."
                )
                self._generate_button.setText(f"Generate layouts of size {output_size}")
            elif self._repeat_columns > 1 or self._repeat_rows > 1:
                output_summary = (
                    f"Repeated output size ({self._repeat_columns} across x "
                    f"{self._repeat_rows} down, {self._repeat_gap_mm:.2f} mm "
                    f"gap; dots only outside): {output_size}."
                )
                self._generate_button.setText(
                    f"Generate repeated files of size {output_size}"
                )
            else:
                output_summary = (
                    f"Expanded output size ({layout_description} dots, "
                    f"{self._safety_distance.value():.2f} mm safety): "
                    f"{output_size}."
                )
                self._generate_button.setText(f"Generate files of size {output_size}")
        else:
            per_page_sizes = "; ".join(
                f"layout {index}: "
                f"{geometry.width_mm:.2f} x {geometry.height_mm:.2f} mm"
                for index, geometry in enumerate(output_geometries, start=1)
            )
            if self._arrange_document_pages:
                output_summary = (
                    f"Output layouts ({self._repeat_columns} across x "
                    f"{self._repeat_rows} down; unused slots blank): "
                    f"{per_page_sizes}."
                )
                self._generate_button.setText(
                    "Generate layouts — page sizes vary"
                )
            elif self._repeat_columns > 1 or self._repeat_rows > 1:
                output_summary = (
                    f"Repeated output sizes ({self._repeat_columns} across x "
                    f"{self._repeat_rows} down, {self._repeat_gap_mm:.2f} mm "
                    f"gap; dots only outside): {per_page_sizes}."
                )
                self._generate_button.setText("Generate repeated files — page sizes vary")
            else:
                output_summary = (
                    f"Expanded output sizes ({layout_description} dots, "
                    f"{self._safety_distance.value():.2f} mm safety): "
                    f"{per_page_sizes}."
                )
                self._generate_button.setText("Generate files — page sizes vary")

        self._summary_label.setText(
            f"{len(self._pages)} page(s). {source_summary} {cut_summary} "
            f"{bleed_summary} "
            f"Safety distance: {self._safety_distance.value():.2f} mm."
        )
        self._output_size_label.setText(output_summary)
        self._refresh_live_preview()
        self._generate_button.setEnabled(
            generation_valid
            and self._source_path is not None
            and self._output_directory is not None
            and bool(selected)
        )

    def _refresh_live_preview(self, *_args: object) -> None:
        selected = self._selected_boxes()
        if not selected:
            return
        if self._actual_preview_checkbox.isChecked():
            self._preview_error = None

        layout = "left_right" if self._left_right_layout.isChecked() else "top_bottom"
        dimensions = [page.boxes[box_name] for page, box_name in selected]
        selector = self._live_preview_selector
        previous_selection = selector.currentData()
        selector.blockSignals(True)
        selector.clear()
        try:
            layout_dimensions = self._layout_boxes(selected)
            layout_gap_mm = self._layout_gap_mm()
        except ValueError as error:
            self._live_preview_details.setText(str(error))
            self._live_preview_widget.clear_preview()
            selector.addItem("Bleed unavailable", 0)
            selector.blockSignals(False)
            selector.setVisible(False)
            if self._expanded_preview_window is not None:
                self._expanded_preview_window.update_preview(
                    self._live_preview_widget,
                    self._live_preview_details.text(),
                )
            return

        if self._repeat_pages_individually:
            page_copy_counts = self._selected_page_copy_counts()
            try:
                geometry = repeated_document_layout_geometry(
                    layout_dimensions,
                    page_copy_counts,
                    layout,
                    self._safety_distance.value(),
                    self._repeat_columns,
                    self._repeat_rows,
                    layout_gap_mm,
                )
            except ValueError as error:
                self._live_preview_details.setText(str(error))
                self._live_preview_widget.clear_preview()
                selector.addItem("Grid does not fit", 0)
            else:
                self._set_live_document_preview(
                    selected,
                    geometry,
                    "per-page copies in configured grid; unused cells blank",
                )
                selector.addItem("Page-copy layout", 0)
        elif self._arrange_document_pages:
            layouts = document_layout_geometries(
                layout_dimensions,
                layout,
                self._safety_distance.value(),
                self._repeat_columns,
                self._repeat_rows,
                layout_gap_mm,
            )
            for layout_index, document_layout in enumerate(layouts):
                page_numbers = [
                    selected[placement.page_index][0].page_number
                    for placement in document_layout.placements
                ]
                selector.addItem(
                    f"Layout {layout_index + 1}: pages "
                    f"{page_numbers[0]}–{page_numbers[-1]}",
                    layout_index,
                )
            index = (
                previous_selection
                if isinstance(previous_selection, int)
                and 0 <= previous_selection < len(layouts)
                else 0
            )
            selector.setCurrentIndex(index)
            self._set_live_document_preview(
                selected,
                layouts[index],
                "unused slots blank",
            )
        else:
            for page_index, (page, box_name) in enumerate(selected):
                selector.addItem(
                    f"Page {page.page_number} — {box_name}",
                    page_index,
                )
            index = (
                previous_selection
                if isinstance(previous_selection, int)
                and 0 <= previous_selection < len(selected)
                else 0
            )
            selector.setCurrentIndex(index)
            page, box_name = selected[index]
            box = page.boxes[box_name]
            layout_box = layout_dimensions[index]
            geometry = repeated_output_geometry(
                layout_box,
                layout,
                self._safety_distance.value(),
                self._repeat_columns,
                self._repeat_rows,
                layout_gap_mm,
            )
            cut_width, cut_height, cut_status = self._preview_cut_size(box)
            page_number = page.page_number
            page_image = (
                self._preview_page_image(page_number, layout_box)
                if self._actual_preview_checkbox.isChecked()
                else None
            )
            self._live_preview_widget.set_preview(
                layout_box,
                geometry,
                layout,
                cut_width,
                cut_height,
                self._repeat_columns,
                self._repeat_rows,
                layout_gap_mm,
                tiles=[
                    (
                        layout_box,
                        column * (layout_box.width_mm + layout_gap_mm),
                        row * (layout_box.height_mm + layout_gap_mm),
                        cut_width,
                        cut_height,
                        page_number,
                        page_image,
                    )
                    for row in range(self._repeat_rows)
                    for column in range(self._repeat_columns)
                ],
                show_page_numbers=len(self._pages) > 1,
            )
            self._live_preview_details.setText(
                f"Output: {geometry.width_mm:.2f} x {geometry.height_mm:.2f} mm | "
                f"Page {page.page_number}: {box.format_mm()} | "
                + (
                    f"True bleed: {self._true_bleed_amount.value():.2f} mm per side; "
                    f"{layout_gap_mm:.2f} mm printed gap | "
                    if self._true_bleed_checkbox.isChecked()
                    else ""
                )
                + f"Repeat: {self._repeat_columns} x {self._repeat_rows}, "
                f"{self._repeat_gap_mm:.2f} mm gap | {cut_status}"
                + (
                    f" | {self._preview_error}"
                    if self._actual_preview_checkbox.isChecked()
                    and self._preview_error is not None
                    else ""
                )
            )

        selector.blockSignals(False)
        selector.setVisible(selector.count() > 1)
        if self._expanded_preview_window is not None:
            self._expanded_preview_window.update_preview(
                self._live_preview_widget,
                self._live_preview_details.text(),
            )

    def _preview_page_image(
        self,
        page_number: int,
        box: BoxDimensions,
    ) -> QImage | None:
        if page_number in self._preview_image_cache:
            media_image = self._preview_image_cache[page_number]
        else:
            document = self._pdf_preview_document
            if document is None:
                return None

            page_index = page_number - 1
            page_size = document.pagePointSize(page_index)
            if page_size.width() <= 0 or page_size.height() <= 0:
                self._preview_error = (
                    f"Could not determine size for page {page_number}."
                )
                return None
            scale = 600 / max(page_size.width(), page_size.height())
            media_image = document.render(
                page_index,
                QSize(
                    max(1, round(page_size.width() * scale)),
                    max(1, round(page_size.height() * scale)),
                ),
            )
            if media_image.isNull():
                self._preview_error = f"Could not render page {page_number}."
                return None
            self._preview_image_cache[page_number] = media_image

        media_box = self._pages[page_number - 1].boxes["MediaBox"]
        if media_box.width_points <= 0 or media_box.height_points <= 0:
            self._preview_error = f"Could not determine bounds for page {page_number}."
            return None

        scale_x = media_image.width() / media_box.width_points
        scale_y = media_image.height() / media_box.height_points
        left = round((box.left - media_box.left) * scale_x)
        top = round((media_box.top - box.top) * scale_y)
        right = round((box.right - media_box.left) * scale_x)
        bottom = round((media_box.top - box.bottom) * scale_y)
        left = min(max(left, 0), media_image.width() - 1)
        top = min(max(top, 0), media_image.height() - 1)
        right = min(max(right, left + 1), media_image.width())
        bottom = min(max(bottom, top + 1), media_image.height())
        return media_image.copy(left, top, right - left, bottom - top)

    def _preview_cut_size(self, box: BoxDimensions) -> tuple[float, float, str]:
        if not self._fake_bleed_checkbox.isChecked():
            return box.width_mm, box.height_mm, "no fake bleed"
        inset = self._fake_bleed_inset.value()
        try:
            width, height = cutter_rectangle_size(box, inset)
        except ValueError:
            return 0.0, 0.0, f"invalid {inset:.2f} mm inset"
        return width, height, (
            f"{inset:.2f} mm inset per side; cutter "
            f"{width:.2f} x {height:.2f} mm"
        )

    def _set_live_document_preview(
        self,
        selected: list[tuple[PageDimensions, str]],
        document_layout: DocumentLayoutGeometry,
        layout_note: str,
    ) -> None:
        tiles = []
        layout_dimensions = self._layout_boxes(selected)
        for placement in document_layout.placements:
            page, box_name = selected[placement.page_index]
            box = page.boxes[box_name]
            layout_box = layout_dimensions[placement.page_index]
            cut_width, cut_height, _ = self._preview_cut_size(box)
            page_image = (
                self._preview_page_image(page.page_number, layout_box)
                if self._actual_preview_checkbox.isChecked()
                else None
            )
            tiles.append(
                (
                    layout_box,
                    placement.offset_x_mm,
                    placement.offset_y_mm,
                    cut_width,
                    cut_height,
                    page.page_number,
                    page_image,
                )
            )
        first_page = selected[document_layout.placements[0].page_index][0].page_number
        last_page = selected[document_layout.placements[-1].page_index][0].page_number
        geometry = OutputPageGeometry(
            document_layout.width_mm,
            document_layout.height_mm,
            document_layout.source_offset_x_mm,
            document_layout.source_offset_y_mm,
        )
        self._live_preview_widget.set_preview(
            tiles[0][0],
            geometry,
            "left_right" if self._left_right_layout.isChecked() else "top_bottom",
            tiles[0][3],
            tiles[0][4],
            tiles=tiles,
            layout_extent_mm=(
                document_layout.grid_width_mm,
                document_layout.grid_height_mm,
            ),
            show_page_numbers=len(self._pages) > 1,
        )
        cut_statuses = [
            self._preview_cut_size(page.boxes[box_name])[2]
            for page, box_name in selected
        ]
        cut_status = (
            "invalid fake bleed inset for at least one source page"
            if any(status.startswith("invalid") for status in cut_statuses)
            else (
                f"fake bleed inset {self._fake_bleed_inset.value():.2f} mm per side"
                if self._fake_bleed_checkbox.isChecked()
                else "no fake bleed"
            )
        )
        copy_counts = [
            sum(
                placement.page_index == page_index
                for placement in document_layout.placements
            )
            for page_index in range(len(selected))
        ]
        copy_summary = ", ".join(
            f"p{page.page_number} x{count}"
            for (page, _), count in zip(selected, copy_counts)
            if count
        )
        self._live_preview_details.setText(
            f"Output: {geometry.width_mm:.2f} x {geometry.height_mm:.2f} mm | "
            f"Pages {first_page}–{last_page} | {copy_summary} | "
            f"{layout_note} | {cut_status}"
            + (
                f" | true bleed {self._true_bleed_amount.value():.2f} mm per side, "
                f"{self._layout_gap_mm():.2f} mm printed gap"
                if self._true_bleed_checkbox.isChecked()
                else ""
            )
            + (
                f" | {self._preview_error}"
                if self._actual_preview_checkbox.isChecked()
                and self._preview_error is not None
                else ""
            )
        )

    def _repeat_settings_changed(self, *_args: object) -> None:
        self._repeat_columns = self._repeat_columns_control.value()
        self._repeat_rows = self._repeat_rows_control.value()
        self._repeat_gap_mm = self._repeat_gap_control.value()
        self._refresh_summary()

    def _set_arrange_document_pages(self, enabled: bool) -> None:
        self._arrange_document_pages = enabled
        if enabled:
            self._repeat_pages_checkbox.setChecked(False)
        self._refresh_summary()

    def _set_repeat_pages_individually(self, enabled: bool) -> None:
        self._repeat_pages_individually = enabled
        if enabled:
            self._arrange_pages_checkbox.setChecked(False)
        for row in range(self._dimensions_table.rowCount()):
            control = self._dimensions_table.cellWidget(
                row,
                len(BOX_NAMES) + 2,
            )
            if isinstance(control, QSpinBox):
                control.setEnabled(enabled)
        self._refresh_summary()

    def _open_expanded_preview(self) -> None:
        if self._expanded_preview_window is None:
            self._expanded_preview_window = ExpandedPreviewWindow(self)
        self._expanded_preview_window.update_preview(
            self._live_preview_widget,
            self._live_preview_details.text(),
        )
        self._expanded_preview_window.show()
        self._expanded_preview_window.raise_()
        self._expanded_preview_window.activateWindow()

    def _generate_files(self) -> None:
        if self._source_path is None or self._output_directory is None:
            QMessageBox.warning(
                self,
                "Choose input and output",
                "Choose a source PDF and an output folder before generating files.",
            )
            return

        selected = self._selected_boxes()
        layout = "left_right" if self._left_right_layout.isChecked() else "top_bottom"
        try:
            dimensions = self._layout_boxes(selected)
            layout_gap_mm = self._layout_gap_mm()
        except ValueError as error:
            QMessageBox.warning(self, "True bleed unavailable", str(error))
            return
        page_copy_counts = self._selected_page_copy_counts()
        if self._repeat_pages_individually:
            try:
                output_geometry = repeated_document_layout_geometry(
                    dimensions,
                    page_copy_counts,
                    layout,
                    self._safety_distance.value(),
                    self._repeat_columns,
                    self._repeat_rows,
                    layout_gap_mm,
                )
            except ValueError as error:
                QMessageBox.warning(self, "Page copies do not fit", str(error))
                return
            output_geometries = [output_geometry]
        elif self._arrange_document_pages:
            output_geometries = document_layout_geometries(
                dimensions,
                layout,
                self._safety_distance.value(),
                self._repeat_columns,
                self._repeat_rows,
                layout_gap_mm,
            )
        else:
            output_geometries = [
                repeated_output_geometry(
                    box,
                    layout,
                    self._safety_distance.value(),
                    self._repeat_columns,
                    self._repeat_rows,
                    layout_gap_mm,
                )
                for box in dimensions
            ]
        output_sizes = sorted(
            {
                (round(geometry.width_mm, 2), round(geometry.height_mm, 2))
                for geometry in output_geometries
            }
        )
        print_path, cut_path = output_file_paths(
            self._source_path,
            self._output_directory,
            self._job_description.text(),
            output_sizes,
        )
        if self._source_path.resolve() in (print_path.resolve(), cut_path.resolve()):
            QMessageBox.critical(
                self,
                "Output would overwrite source",
                "The selected output folder and filename would overwrite the "
                "source PDF. Choose another output folder or job description.",
            )
            return

        existing_paths = [path for path in (print_path, cut_path) if path.exists()]
        if existing_paths:
            answer = QMessageBox.question(
                self,
                "Replace existing files?",
                "The following output file(s) already exist. Replace them?\n\n"
                + "\n".join(str(path) for path in existing_paths),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            generate_pdf_pair(
                self._source_path,
                print_path,
                cut_path,
                [box_name for _, box_name in selected],
                layout,
                self._safety_distance.value(),
                self._fake_bleed_checkbox.isChecked(),
                self._fake_bleed_inset.value(),
                self._repeat_columns,
                self._repeat_rows,
                self._repeat_gap_mm,
                self._arrange_document_pages,
                page_copy_counts if self._repeat_pages_individually else None,
                self._true_bleed_checkbox.isChecked(),
                self._true_bleed_amount.value(),
            )
        except (OSError, pikepdf.PdfError, ValueError) as error:
            QMessageBox.critical(self, "Could not generate PDFs", str(error))
            return

        QMessageBox.information(
            self,
            "PDFs generated",
            f"Printer PDF:\n{print_path}\n\nCutter PDF:\n{cut_path}",
        )
