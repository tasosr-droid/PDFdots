from dataclasses import dataclass
from pathlib import Path

import pikepdf


POINTS_PER_MM = 72 / 25.4
DEFAULT_REPEAT_GAP_MM = 5.0
BOX_NAMES = ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")


@dataclass(frozen=True)
class BoxDimensions:
    name: str
    left: float
    bottom: float
    right: float
    top: float

    @property
    def width_points(self) -> float:
        return self.right - self.left

    @property
    def height_points(self) -> float:
        return self.top - self.bottom

    @property
    def width_mm(self) -> float:
        return self.width_points / POINTS_PER_MM

    @property
    def height_mm(self) -> float:
        return self.height_points / POINTS_PER_MM

    def format_mm(self) -> str:
        return f"{self.width_mm:.2f} x {self.height_mm:.2f} mm"


@dataclass(frozen=True)
class PageDimensions:
    page_number: int
    boxes: dict[str, BoxDimensions]


@dataclass(frozen=True)
class OutputPageGeometry:
    width_mm: float
    height_mm: float
    source_offset_x_mm: float
    source_offset_y_mm: float


@dataclass(frozen=True)
class DocumentPagePlacement:
    page_index: int
    offset_x_mm: float
    offset_y_mm: float


@dataclass(frozen=True)
class DocumentLayoutGeometry:
    width_mm: float
    height_mm: float
    source_offset_x_mm: float
    source_offset_y_mm: float
    grid_width_mm: float
    grid_height_mm: float
    placements: tuple[DocumentPagePlacement, ...]


def cutter_rectangle_size(
    box: BoxDimensions,
    fake_bleed_inset_mm: float = 0.0,
) -> tuple[float, float]:
    """Return the selected box size after a uniform cutter-rectangle inset."""
    if fake_bleed_inset_mm < 0:
        raise ValueError("Fake bleed inset cannot be negative.")
    width = box.width_mm - 2 * fake_bleed_inset_mm
    height = box.height_mm - 2 * fake_bleed_inset_mm
    if width <= 0 or height <= 0:
        raise ValueError("Fake bleed inset is too large for the selected page.")
    return width, height


def expanded_bleed_box(
    box: BoxDimensions,
    media_box: BoxDimensions,
    bleed_mm: float,
) -> BoxDimensions:
    """Return the source clip box extended into existing artwork on each side."""
    if bleed_mm < 0:
        raise ValueError("True bleed cannot be negative.")
    bleed_points = bleed_mm * POINTS_PER_MM
    expanded = BoxDimensions(
        name=box.name,
        left=box.left - bleed_points,
        bottom=box.bottom - bleed_points,
        right=box.right + bleed_points,
        top=box.top + bleed_points,
    )
    if (
        expanded.left < media_box.left - 1e-6
        or expanded.bottom < media_box.bottom - 1e-6
        or expanded.right > media_box.right + 1e-6
        or expanded.top > media_box.top + 1e-6
    ):
        raise ValueError(
            f"{bleed_mm:.2f} mm true bleed extends beyond the page's MediaBox "
            f"outside the selected {box.name}."
        )
    return expanded


def true_bleed_gap_mm(repeat_gap_mm: float, bleed_mm: float) -> float:
    """Keep the configured gap between cut boxes after artwork is expanded."""
    image_gap_mm = repeat_gap_mm - 2 * bleed_mm
    if image_gap_mm < 0:
        raise ValueError(
            "True bleed would make neighboring printed pages overlap. "
            "Increase the page gap or reduce the bleed amount."
        )
    return image_gap_mm


def expanded_output_geometry(
    box: BoxDimensions,
    layout: str,
    safety_distance_mm: float,
    dot_diameter_mm: float = 6.0,
    outward_offset_mm: float = 8.0,
    corner_offset_mm: float = 3.0,
) -> OutputPageGeometry:
    """Size the expanded sheet to the four dots plus the safety margin."""
    if safety_distance_mm < 0:
        raise ValueError("Safety distance cannot be negative.")
    if dot_diameter_mm <= 0:
        raise ValueError("Dot diameter must be greater than zero.")
    if layout not in ("left_right", "top_bottom"):
        raise ValueError(f"Unsupported dot layout: {layout}")

    width = box.width_mm
    height = box.height_mm
    radius = dot_diameter_mm / 2
    if layout == "left_right":
        centers = (
            (-outward_offset_mm, corner_offset_mm),
            (width + outward_offset_mm, corner_offset_mm),
            (width + outward_offset_mm, height - corner_offset_mm),
            (-outward_offset_mm, height - corner_offset_mm),
        )
    else:
        centers = (
            (corner_offset_mm, -outward_offset_mm),
            (corner_offset_mm, height + outward_offset_mm),
            (width - corner_offset_mm, height + outward_offset_mm),
            (width - corner_offset_mm, -outward_offset_mm),
        )

    left = min(x for x, _ in centers) - radius - safety_distance_mm
    right = max(x for x, _ in centers) + radius + safety_distance_mm
    bottom = min(y for _, y in centers) - radius - safety_distance_mm
    top = max(y for _, y in centers) + radius + safety_distance_mm
    return OutputPageGeometry(
        width_mm=right - left,
        height_mm=top - bottom,
        source_offset_x_mm=-left,
        source_offset_y_mm=-bottom,
    )


def repeated_output_geometry(
    box: BoxDimensions,
    layout: str,
    safety_distance_mm: float,
    repeat_columns: int = 1,
    repeat_rows: int = 1,
    repeat_gap_mm: float = DEFAULT_REPEAT_GAP_MM,
) -> OutputPageGeometry:
    """Return the output geometry for a repeated grid of the selected box."""
    if repeat_columns < 1 or repeat_rows < 1:
        raise ValueError("Repeat rows and columns must be at least one.")
    if repeat_gap_mm < 0:
        raise ValueError("Repeat gap cannot be negative.")

    repeated_box = BoxDimensions(
        name=box.name,
        left=0,
        bottom=0,
        right=(
            repeat_columns * box.width_points
            + (repeat_columns - 1) * repeat_gap_mm * POINTS_PER_MM
        ),
        top=(
            repeat_rows * box.height_points
            + (repeat_rows - 1) * repeat_gap_mm * POINTS_PER_MM
        ),
    )
    return expanded_output_geometry(
        repeated_box,
        layout,
        safety_distance_mm,
    )


def document_layout_geometries(
    boxes: list[BoxDimensions],
    layout: str,
    safety_distance_mm: float,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
) -> list[DocumentLayoutGeometry]:
    """Arrange consecutive document pages in row-major fixed-size grids."""
    if not boxes:
        raise ValueError("No pages were selected for output.")
    if repeat_columns < 1 or repeat_rows < 1:
        raise ValueError("Repeat rows and columns must be at least one.")
    if repeat_gap_mm < 0:
        raise ValueError("Repeat gap cannot be negative.")

    capacity = repeat_columns * repeat_rows
    column_widths = [0.0] * repeat_columns
    row_heights = [0.0] * repeat_rows
    for page_index, box in enumerate(boxes):
        slot = page_index % capacity
        column = slot % repeat_columns
        row = slot // repeat_columns
        column_widths[column] = max(column_widths[column], box.width_mm)
        row_heights[row] = max(row_heights[row], box.height_mm)
    fallback_width = max(box.width_mm for box in boxes)
    fallback_height = max(box.height_mm for box in boxes)
    column_widths = [width or fallback_width for width in column_widths]
    row_heights = [height or fallback_height for height in row_heights]

    total_width = sum(column_widths) + (repeat_columns - 1) * repeat_gap_mm
    total_height = sum(row_heights) + (repeat_rows - 1) * repeat_gap_mm
    grid_box = BoxDimensions(
        "Document layout",
        0,
        0,
        total_width * POINTS_PER_MM,
        total_height * POINTS_PER_MM,
    )
    output = expanded_output_geometry(
        grid_box,
        layout,
        safety_distance_mm,
    )

    column_offsets: list[float] = []
    offset = 0.0
    for width in column_widths:
        column_offsets.append(offset)
        offset += width + repeat_gap_mm
    row_offsets: list[float] = []
    offset = total_height
    for height in row_heights:
        offset -= height
        row_offsets.append(offset)
        offset -= repeat_gap_mm

    layouts: list[DocumentLayoutGeometry] = []
    page_count = (len(boxes) + capacity - 1) // capacity
    for layout_index in range(page_count):
        placements: list[DocumentPagePlacement] = []
        first_page = layout_index * capacity
        for page_index in range(first_page, min(first_page + capacity, len(boxes))):
            box = boxes[page_index]
            slot = page_index - first_page
            column = slot % repeat_columns
            row = slot // repeat_columns
            placements.append(
                DocumentPagePlacement(
                    page_index=page_index,
                    offset_x_mm=(
                        column_offsets[column]
                        + (column_widths[column] - box.width_mm) / 2
                    ),
                    offset_y_mm=(
                        row_offsets[row]
                        + (row_heights[row] - box.height_mm) / 2
                    ),
                )
            )
        layouts.append(
            DocumentLayoutGeometry(
                width_mm=output.width_mm,
                height_mm=output.height_mm,
                source_offset_x_mm=output.source_offset_x_mm,
                source_offset_y_mm=output.source_offset_y_mm,
                grid_width_mm=total_width,
                grid_height_mm=total_height,
                placements=tuple(placements),
            )
        )
    return layouts


def repeated_document_layout_geometry(
    boxes: list[BoxDimensions],
    page_copy_counts: list[int],
    layout: str,
    safety_distance_mm: float,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
) -> DocumentLayoutGeometry:
    """Place each requested page copy in a fixed row-major grid."""
    if not boxes:
        raise ValueError("No pages were selected for output.")
    if len(boxes) != len(page_copy_counts):
        raise ValueError("Copy counts must match the selected source pages.")
    if repeat_columns < 1 or repeat_rows < 1:
        raise ValueError("Repeat rows and columns must be at least one.")
    if repeat_gap_mm < 0:
        raise ValueError("Repeat gap cannot be negative.")
    if any(count < 0 for count in page_copy_counts):
        raise ValueError("Page copy counts cannot be negative.")

    capacity = repeat_columns * repeat_rows
    copy_total = sum(page_copy_counts)
    if copy_total == 0:
        raise ValueError("At least one page copy is required.")
    if copy_total > capacity:
        raise ValueError(
            f"The selected pages require {copy_total} grid slots, but the "
            f"layout has only {capacity}."
        )

    column_widths = [0.0] * repeat_columns
    row_heights = [0.0] * repeat_rows
    slot_pages: list[int] = []
    for page_index, copy_count in enumerate(page_copy_counts):
        slot_pages.extend([page_index] * copy_count)
    for slot, page_index in enumerate(slot_pages):
        column = slot % repeat_columns
        row = slot // repeat_columns
        column_widths[column] = max(column_widths[column], boxes[page_index].width_mm)
        row_heights[row] = max(row_heights[row], boxes[page_index].height_mm)
    fallback_width = max(box.width_mm for box in boxes)
    fallback_height = max(box.height_mm for box in boxes)
    column_widths = [width or fallback_width for width in column_widths]
    row_heights = [height or fallback_height for height in row_heights]

    grid_width = sum(column_widths) + (repeat_columns - 1) * repeat_gap_mm
    grid_height = sum(row_heights) + (repeat_rows - 1) * repeat_gap_mm
    grid_box = BoxDimensions(
        "Repeated document layout",
        0,
        0,
        grid_width * POINTS_PER_MM,
        grid_height * POINTS_PER_MM,
    )
    output = expanded_output_geometry(
        grid_box,
        layout,
        safety_distance_mm,
    )

    column_offsets: list[float] = []
    offset = 0.0
    for width in column_widths:
        column_offsets.append(offset)
        offset += width + repeat_gap_mm

    row_offsets: list[float] = []
    offset = grid_height
    for height in row_heights:
        offset -= height
        row_offsets.append(offset)
        offset -= repeat_gap_mm

    placements: list[DocumentPagePlacement] = []
    for slot, page_index in enumerate(slot_pages):
        box = boxes[page_index]
        column = slot % repeat_columns
        row = slot // repeat_columns
        placements.append(
            DocumentPagePlacement(
                page_index=page_index,
                offset_x_mm=(
                    column_offsets[column]
                    + (column_widths[column] - box.width_mm) / 2
                ),
                offset_y_mm=(
                    row_offsets[row]
                    + (row_heights[row] - box.height_mm) / 2
                ),
            )
        )

    return DocumentLayoutGeometry(
        width_mm=output.width_mm,
        height_mm=output.height_mm,
        source_offset_x_mm=output.source_offset_x_mm,
        source_offset_y_mm=output.source_offset_y_mm,
        grid_width_mm=grid_width,
        grid_height_mm=grid_height,
        placements=tuple(placements),
    )


def inspect_pdf(path: Path) -> list[PageDimensions]:
    """Read each page's effective PDF boxes without modifying the source."""
    pages: list[PageDimensions] = []

    with pikepdf.Pdf.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            boxes: dict[str, BoxDimensions] = {}
            for box_name in BOX_NAMES:
                box = getattr(page, box_name.lower())
                left, bottom, right, top = (float(coordinate) for coordinate in box)
                if right <= left or top <= bottom:
                    raise ValueError(
                        f"Page {page_number} has an invalid {box_name}."
                    )
                boxes[box_name] = BoxDimensions(
                    name=box_name,
                    left=left,
                    bottom=bottom,
                    right=right,
                    top=top,
                )
            pages.append(PageDimensions(page_number=page_number, boxes=boxes))

    if not pages:
        raise ValueError("The selected PDF contains no pages.")
    return pages
