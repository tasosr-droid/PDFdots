from collections.abc import Callable
from pathlib import Path

import pikepdf
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QIcon, QPainter, QPen
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
    inspect_pdf,
    repeated_output_geometry,
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
                list[tuple[BoxDimensions, float, float, float, float]],
                float,
                float,
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
        tiles: list[tuple[BoxDimensions, float, float, float, float]] | None = None,
        layout_extent_mm: tuple[float, float] | None = None,
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
        for tile_box, tile_x, tile_y, cut_width_mm, cut_height_mm in tiles:
            tile_x += source_x
            tile_y += source_y
            source_top_left = point(tile_x, tile_y + tile_box.height_mm)
            painter.drawRect(
                source_top_left.x(),
                source_top_left.y(),
                tile_box.width_mm * scale,
                tile_box.height_mm * scale,
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
            painter.setBrush(QColor("#dce6f1"))

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
        self._document_layouts: list[DocumentLayoutGeometry] = (
            document_layout_geometries(
                [page.boxes[box_name] for page, box_name in selected_pages],
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
        if arrange_document_pages:
            capacity = repeat_columns * repeat_rows
            for layout_index, document_layout in enumerate(
                self._document_layouts,
                start=1,
            ):
                page_numbers = [
                    selected_pages[placement.page_index][0].page_number
                    for placement in document_layout.placements
                ]
                self._page_selector.addItem(
                    f"Layout {layout_index} — pages "
                    f"{page_numbers[0]}–{page_numbers[-1]} ({capacity} slots)",
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
            if arrange_document_pages
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
        if self._arrange_document_pages:
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
            self._details_label.setText(
                f"Output: {geometry.width_mm:.2f} x {geometry.height_mm:.2f} mm  |  "
                f"Layout {index + 1}: source pages {first_page}–{last_page}  |  "
                f"{self._repeat_columns} x {self._repeat_rows}, "
                f"{self._repeat_gap_mm:.2f} mm gap; unused slots blank; "
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

        controls.addWidget(QLabel("Pages horizontally:"))
        controls.addWidget(self._columns)
        controls.addWidget(QLabel("Pages vertically:"))
        controls.addWidget(self._rows)
        controls.addWidget(QLabel("Gap:"))
        controls.addWidget(self._gap)
        dialog_layout.addLayout(controls)
        dialog_layout.addWidget(
            QLabel(
                "When off, each source page repeats across its own layout. "
                "When on, consecutive document pages fill the grid row by row. "
                "Unused slots on the final layout stay blank."
            )
        )
        dialog_layout.addWidget(self._arrange_pages)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        dialog_layout.addWidget(buttons)

    @property
    def settings(self) -> tuple[int, int, float, bool]:
        return (
            self._columns.value(),
            self._rows.value(),
            self._gap.value(),
            self._arrange_pages.isChecked(),
        )


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setWindowTitle("PDF Dots v0.4 by Tasos Rizopoulos 2026")
        self.setWindowIcon(QIcon(str(resource_path("dots.png"))))
        self.setMinimumSize(1060, 680)

        self._source_path: Path | None = None
        self._output_directory: Path | None = None
        self._pages: list[PageDimensions] = []
        self._repeat_columns = 1
        self._repeat_rows = 1
        self._repeat_gap_mm = DEFAULT_REPEAT_GAP_MM
        self._arrange_document_pages = False

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

        self._dimensions_table = QTableWidget(0, len(BOX_NAMES) + 2)
        self._dimensions_table.setHorizontalHeaderLabels(
            ["Page", *BOX_NAMES, "Selected source box"]
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
        bleed_group = QGroupBox("Cutter rectangle")
        bleed_layout = QHBoxLayout(bleed_group)
        bleed_layout.addWidget(self._fake_bleed_checkbox)
        bleed_layout.addWidget(QLabel("Inset on each side:"))
        bleed_layout.addWidget(self._fake_bleed_inset)
        bleed_layout.addWidget(QLabel("mm"))
        bleed_layout.addStretch()

        self._summary_label = QLabel(
            "Load a PDF to inspect its page boxes and resulting cut size."
        )
        self._summary_label.setWordWrap(True)
        self._output_size_label = QLabel(
            "Expanded output size will be shown here after loading a PDF."
        )
        self._output_size_label.setWordWrap(True)
        self._repeat_layout_button = QPushButton(
            f"Repeat layout: 1 x 1 ({DEFAULT_REPEAT_GAP_MM:.2f} mm gap)"
        )
        self._repeat_layout_button.clicked.connect(self._edit_repeat_layout)
        self._generate_button = QPushButton("Generate files of size —")
        self._generate_button.setEnabled(False)
        self._generate_button.clicked.connect(self._generate_files)
        self._preview_button = QPushButton("Preview layout")
        self._preview_button.setEnabled(False)
        self._preview_button.clicked.connect(self._show_preview)

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
        left_layout.addWidget(self._summary_label)
        left_layout.addWidget(self._output_size_label)
        left_layout.addWidget(self._repeat_layout_button)
        button_row = QHBoxLayout()
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._generate_button, stretch=1)
        left_layout.addLayout(button_row)
        left_layout.addStretch()

        left_scroll_area = QScrollArea()
        left_scroll_area.setWidgetResizable(True)
        left_scroll_area.setWidget(left_panel)
        root_layout.addWidget(left_scroll_area, stretch=1)
        root_layout.addWidget(self._dimensions_group, stretch=1)
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

        self._source_path = path
        self._pages = pages
        self._source_field.setText(str(path))
        self._populate_dimensions_table()
        self._update_box_selection_mode()
        self._refresh_summary()

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

    def _refresh_summary(self, *_args: object) -> None:
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
                generation_valid = True
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
            generation_valid = True
            cut_summary = (
                "Cutter rectangle matches the selected source box; fake bleed "
                "is off."
            )

        layout = "left_right" if self._left_right_layout.isChecked() else "top_bottom"
        layout_description = (
            "left and right" if layout == "left_right" else "top and bottom"
        )
        if self._arrange_document_pages:
            output_geometries = document_layout_geometries(
                dimensions,
                layout,
                self._safety_distance.value(),
                self._repeat_columns,
                self._repeat_rows,
                self._repeat_gap_mm,
            )
            output_sizes = {
                (round(item.width_mm, 2), round(item.height_mm, 2))
                for item in output_geometries
            }
        else:
            output_geometries = [
                repeated_output_geometry(
                    box,
                    layout,
                    self._safety_distance.value(),
                    self._repeat_columns,
                    self._repeat_rows,
                    self._repeat_gap_mm,
                )
                for box in dimensions
            ]
            output_sizes = {
                (round(item.width_mm, 2), round(item.height_mm, 2))
                for item in output_geometries
            }
        if len(output_sizes) == 1:
            output_width, output_height = next(iter(output_sizes))
            output_size = f"{output_width:.2f} x {output_height:.2f} mm"
            if self._arrange_document_pages:
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
            f"Safety distance: {self._safety_distance.value():.2f} mm."
        )
        self._output_size_label.setText(output_summary)
        self._generate_button.setEnabled(
            generation_valid
            and self._source_path is not None
            and self._output_directory is not None
            and bool(selected)
        )
        self._preview_button.setEnabled(generation_valid and bool(selected))

    def _show_preview(self) -> None:
        selected = self._selected_boxes()
        if not selected:
            return
        layout = "left_right" if self._left_right_layout.isChecked() else "top_bottom"
        dialog = OutputPreviewDialog(
            selected,
            layout,
            self._safety_distance.value(),
            self._fake_bleed_checkbox.isChecked(),
            self._fake_bleed_inset.value(),
            self,
            self._repeat_columns,
            self._repeat_rows,
            self._repeat_gap_mm,
            self._arrange_document_pages,
        )
        dialog.exec()

    def _edit_repeat_layout(self) -> None:
        dialog = RepeatLayoutDialog(
            self._repeat_columns,
            self._repeat_rows,
            self._repeat_gap_mm,
            self,
            self._arrange_document_pages,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        (
            self._repeat_columns,
            self._repeat_rows,
            self._repeat_gap_mm,
            self._arrange_document_pages,
        ) = dialog.settings
        if self._arrange_document_pages:
            self._repeat_layout_button.setText(
                f"Arrange document: {self._repeat_columns} x "
                f"{self._repeat_rows} ({self._repeat_gap_mm:.2f} mm gap)"
            )
        else:
            self._repeat_layout_button.setText(
                f"Repeat layout: {self._repeat_columns} x {self._repeat_rows} "
                f"({self._repeat_gap_mm:.2f} mm gap)"
            )
        self._refresh_summary()

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
        dimensions = [page.boxes[box_name] for page, box_name in selected]
        if self._arrange_document_pages:
            output_geometries = document_layout_geometries(
                dimensions,
                layout,
                self._safety_distance.value(),
                self._repeat_columns,
                self._repeat_rows,
                self._repeat_gap_mm,
            )
        else:
            output_geometries = [
                repeated_output_geometry(
                    box,
                    layout,
                    self._safety_distance.value(),
                    self._repeat_columns,
                    self._repeat_rows,
                    self._repeat_gap_mm,
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
            )
        except (OSError, pikepdf.PdfError, ValueError) as error:
            QMessageBox.critical(self, "Could not generate PDFs", str(error))
            return

        QMessageBox.information(
            self,
            "PDFs generated",
            f"Printer PDF:\n{print_path}\n\nCutter PDF:\n{cut_path}",
        )
