import math
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

import pikepdf

from pdfdots.pdf_inspection import (
    BoxDimensions,
    DEFAULT_REPEAT_GAP_MM,
    DocumentLayoutGeometry,
    OutputPageGeometry,
    POINTS_PER_MM,
    cutter_rectangle_size,
    document_layout_geometries,
    expanded_bleed_box,
    repeated_output_geometry,
    repeated_document_layout_geometry,
    true_bleed_gap_mm,
)

DotLayout = Literal["left_right", "top_bottom"]


def output_file_paths(
    source_path: Path,
    output_directory: Path,
    job_description: str,
    output_sizes: list[tuple[float, float]],
) -> tuple[Path, Path]:
    if not output_sizes:
        raise ValueError("No pages were selected for output.")
    distinct_sizes = sorted(
        {(round(width, 2), round(height, 2)) for width, height in output_sizes}
    )
    if len(distinct_sizes) == 1:
        size_suffix = (
            f"{_filename_dimension(distinct_sizes[0][0])}x"
            f"{_filename_dimension(distinct_sizes[0][1])}mm"
        )
    else:
        size_suffix = "variable-size"

    description = _safe_filename_part(job_description)
    name_parts = [source_path.stem]
    if description:
        name_parts.append(description)
    base = "_".join(name_parts)
    print_path = output_directory / f"{base}_PRINT_{size_suffix}.pdf"
    cut_path = output_directory / f"{base}_CUT_{size_suffix}.pdf"
    return print_path, cut_path


def generate_pdf_pair(
    source_path: Path,
    print_path: Path,
    cut_path: Path,
    box_names: list[str],
    layout: DotLayout,
    safety_distance_mm: float,
    fake_bleed_enabled: bool,
    fake_bleed_inset_mm: float,
    repeat_columns: int = 1,
    repeat_rows: int = 1,
    repeat_gap_mm: float = DEFAULT_REPEAT_GAP_MM,
    arrange_document_pages: bool = False,
    page_copy_counts: list[int] | None = None,
    true_bleed_enabled: bool = False,
    true_bleed_mm: float = 2.0,
) -> None:
    if len(box_names) == 0:
        raise ValueError("The source PDF contains no pages.")
    if print_path.resolve() == source_path.resolve():
        raise ValueError("The printer output cannot overwrite the source PDF.")
    if cut_path.resolve() == source_path.resolve():
        raise ValueError("The cutter output cannot overwrite the source PDF.")
    if print_path.resolve() == cut_path.resolve():
        raise ValueError("Printer and cutter output paths must be different.")

    print_path.parent.mkdir(parents=True, exist_ok=True)
    cut_path.parent.mkdir(parents=True, exist_ok=True)
    print_temp = _temporary_path(print_path)
    cut_temp = _temporary_path(cut_path)
    try:
        _build_pdf_pair(
            source_path,
            print_temp,
            cut_temp,
            box_names,
            layout,
            safety_distance_mm,
            fake_bleed_enabled,
            fake_bleed_inset_mm,
            repeat_columns,
            repeat_rows,
            repeat_gap_mm,
            arrange_document_pages,
            page_copy_counts,
            true_bleed_enabled,
            true_bleed_mm,
        )
        os.replace(print_temp, print_path)
        os.replace(cut_temp, cut_path)
    finally:
        print_temp.unlink(missing_ok=True)
        cut_temp.unlink(missing_ok=True)


def _build_pdf_pair(
    source_path: Path,
    print_temp: Path,
    cut_temp: Path,
    box_names: list[str],
    layout: DotLayout,
    safety_distance_mm: float,
    fake_bleed_enabled: bool,
    fake_bleed_inset_mm: float,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
    arrange_document_pages: bool,
    page_copy_counts: list[int] | None,
    true_bleed_enabled: bool,
    true_bleed_mm: float,
) -> None:
    has_adjacent_pages = (
        sum(page_copy_counts) > 1
        if page_copy_counts is not None
        else (
            arrange_document_pages
            and len(box_names) > 1
            and repeat_columns * repeat_rows > 1
        )
        or (
            not arrange_document_pages
            and (repeat_columns > 1 or repeat_rows > 1)
        )
    )
    layout_gap_mm = (
        true_bleed_gap_mm(repeat_gap_mm, true_bleed_mm)
        if true_bleed_enabled and has_adjacent_pages
        else repeat_gap_mm
    )
    with pikepdf.Pdf.open(source_path) as source_pdf, pikepdf.Pdf.new() as cut_pdf:
        if len(source_pdf.pages) != len(box_names):
            raise ValueError("Source page count changed; reload the PDF and try again.")

        if arrange_document_pages or page_copy_counts is not None:
            if arrange_document_pages and page_copy_counts is not None:
                raise ValueError(
                    "Document arrangement and individual page copies cannot "
                    "be enabled together."
                )
            boxes = []
            layout_boxes = []
            for index, (source_page, box_name) in enumerate(
                zip(source_pdf.pages, box_names), start=1
            ):
                if box_name not in (
                    "MediaBox",
                    "CropBox",
                    "BleedBox",
                    "TrimBox",
                    "ArtBox",
                ):
                    raise ValueError(f"Unsupported source box: {box_name}")
                if source_page.rotation % 360 != 0:
                    raise ValueError(
                        f"Page {index} is rotated. Rotated pages are not supported yet; "
                        "save an upright copy of the PDF and reload it."
                    )
                box = _box_dimensions(source_page, box_name)
                boxes.append(box)
                layout_boxes.append(
                    expanded_bleed_box(
                        box,
                        _box_dimensions(source_page, "MediaBox"),
                        true_bleed_mm,
                    )
                    if true_bleed_enabled
                    else box
                )
            layouts = (
                [
                    repeated_document_layout_geometry(
                        layout_boxes,
                        page_copy_counts,
                        layout,
                        safety_distance_mm,
                        repeat_columns,
                        repeat_rows,
                        layout_gap_mm,
                    )
                ]
                if page_copy_counts is not None
                else document_layout_geometries(
                    layout_boxes,
                    layout,
                    safety_distance_mm,
                    repeat_columns,
                    repeat_rows,
                    layout_gap_mm,
                )
            )
            with pikepdf.Pdf.new() as print_pdf:
                _build_document_layout_pair(
                    source_pdf,
                    print_pdf,
                    cut_pdf,
                    boxes,
                    layout_boxes,
                    layouts,
                    layout,
                    fake_bleed_enabled,
                    fake_bleed_inset_mm,
                )
                print_pdf.save(print_temp)
            cut_pdf.save(cut_temp)
            return

        for index, (source_page, box_name) in enumerate(
            zip(source_pdf.pages, box_names), start=1
        ):
            if box_name not in ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox"):
                raise ValueError(f"Unsupported source box: {box_name}")
            if source_page.rotation % 360 != 0:
                raise ValueError(
                    f"Page {index} is rotated. Rotated pages are not supported yet; "
                    "save an upright copy of the PDF and reload it."
                )

            box = _box_dimensions(source_page, box_name)
            layout_box = (
                expanded_bleed_box(
                    box,
                    _box_dimensions(source_page, "MediaBox"),
                    true_bleed_mm,
                )
                if true_bleed_enabled
                else box
            )
            geometry = repeated_output_geometry(
                layout_box,
                layout,
                safety_distance_mm,
                repeat_columns,
                repeat_rows,
                layout_gap_mm,
            )
            cut_width_mm, cut_height_mm = (
                cutter_rectangle_size(box, fake_bleed_inset_mm)
                if fake_bleed_enabled
                else (box.width_mm, box.height_mm)
            )
            _prepare_printer_page(
                source_pdf,
                source_page,
                layout_box,
                geometry,
                layout,
                repeat_columns,
                repeat_rows,
                layout_gap_mm,
            )
            _create_cutter_page(
                cut_pdf,
                geometry,
                layout_box,
                layout,
                cut_width_mm,
                cut_height_mm,
                repeat_columns,
                repeat_rows,
                layout_gap_mm,
            )

        source_pdf.save(print_temp)
        cut_pdf.save(cut_temp)


def _build_document_layout_pair(
    source_pdf: pikepdf.Pdf,
    print_pdf: pikepdf.Pdf,
    cut_pdf: pikepdf.Pdf,
    boxes: list[BoxDimensions],
    layout_boxes: list[BoxDimensions],
    layouts: list[DocumentLayoutGeometry],
    layout: DotLayout,
    fake_bleed_enabled: bool,
    fake_bleed_inset_mm: float,
) -> None:
    for layout_geometry in layouts:
        print_page = print_pdf.add_blank_page(
            page_size=(
                layout_geometry.width_mm * POINTS_PER_MM,
                layout_geometry.height_mm * POINTS_PER_MM,
            )
        )
        xobjects = pikepdf.Dictionary()
        annotation_array = pikepdf.Array()
        commands = bytearray()
        for placement in layout_geometry.placements:
            source_page = source_pdf.pages[placement.page_index]
            box = layout_boxes[placement.page_index]
            box_array = pikepdf.Array(
                [box.left, box.bottom, box.right, box.top]
            )
            page_box_keys = (
                "/MediaBox",
                "/CropBox",
                "/BleedBox",
                "/TrimBox",
                "/ArtBox",
            )
            original_boxes = {
                key: source_page.obj.get(key)
                for key in page_box_keys
            }
            for key in page_box_keys:
                source_page.obj[key] = box_array
            try:
                source_form = source_page.as_form_xobject()
            finally:
                for key, original_box in original_boxes.items():
                    if original_box is None:
                        del source_page.obj[key]
                    else:
                        source_page.obj[key] = original_box
            form = print_pdf.copy_foreign(source_form)
            resource_name = f"/Page{placement.page_index + 1}"
            xobjects[resource_name] = form
            translate_x = (
                layout_geometry.source_offset_x_mm + placement.offset_x_mm
            ) * POINTS_PER_MM - box.left
            translate_y = (
                layout_geometry.source_offset_y_mm + placement.offset_y_mm
            ) * POINTS_PER_MM - box.bottom
            commands.extend(
                (
                    f"q 1 0 0 1 {_pdf_number(translate_x)} "
                    f"{_pdf_number(translate_y)} cm {resource_name} Do Q\n"
                ).encode("ascii")
            )
            annotations = source_page.obj.get("/Annots")
            if annotations is not None:
                for source_annotation in annotations:
                    annotation = print_pdf.copy_foreign(source_annotation)
                    _translate_annotation(annotation, translate_x, translate_y)
                    annotation["/P"] = print_page.obj
                    annotation_array.append(annotation)

        print_page.obj["/Resources"] = pikepdf.Dictionary(
            {"/XObject": xobjects}
        )
        if annotation_array:
            print_page.obj["/Annots"] = annotation_array
        grid_box = BoxDimensions(
            "Document layout",
            0,
            0,
            layout_geometry.grid_width_mm * POINTS_PER_MM,
            layout_geometry.grid_height_mm * POINTS_PER_MM,
        )
        commands.extend(
            _dot_drawing_commands(
                grid_box,
                layout,
                layout_geometry.source_offset_x_mm * POINTS_PER_MM,
                layout_geometry.source_offset_y_mm * POINTS_PER_MM,
            )
        )
        print_page.contents_add(print_pdf.make_stream(bytes(commands)))

        _create_document_cutter_page(
            cut_pdf,
            layout_geometry,
            boxes,
            layout_boxes,
            layout,
            fake_bleed_enabled,
            fake_bleed_inset_mm,
        )


def _create_document_cutter_page(
    pdf: pikepdf.Pdf,
    geometry: DocumentLayoutGeometry,
    boxes: list[BoxDimensions],
    layout_boxes: list[BoxDimensions],
    layout: DotLayout,
    fake_bleed_enabled: bool,
    fake_bleed_inset_mm: float,
) -> None:
    page = pdf.add_blank_page(
        page_size=(
            geometry.width_mm * POINTS_PER_MM,
            geometry.height_mm * POINTS_PER_MM,
        )
    )
    grid_box = BoxDimensions(
        "Document layout",
        0,
        0,
        geometry.grid_width_mm * POINTS_PER_MM,
        geometry.grid_height_mm * POINTS_PER_MM,
    )
    commands = bytearray(
        _dot_drawing_commands(
            grid_box,
            layout,
            geometry.source_offset_x_mm * POINTS_PER_MM,
            geometry.source_offset_y_mm * POINTS_PER_MM,
        )
    )
    for placement in geometry.placements:
        box = boxes[placement.page_index]
        layout_box = layout_boxes[placement.page_index]
        cut_width_mm, cut_height_mm = (
            cutter_rectangle_size(box, fake_bleed_inset_mm)
            if fake_bleed_enabled
            else (box.width_mm, box.height_mm)
        )
        cut_width = cut_width_mm * POINTS_PER_MM
        cut_height = cut_height_mm * POINTS_PER_MM
        cut_x = (
            geometry.source_offset_x_mm
            + placement.offset_x_mm
            + (layout_box.width_mm - cut_width_mm) / 2
        ) * POINTS_PER_MM
        cut_y = (
            geometry.source_offset_y_mm
            + placement.offset_y_mm
            + (layout_box.height_mm - cut_height_mm) / 2
        ) * POINTS_PER_MM
        commands.extend(
            (
                f"0 0 0 RG 0.75 w {_pdf_number(cut_x)} {_pdf_number(cut_y)} "
                f"{_pdf_number(cut_width)} {_pdf_number(cut_height)} re S\n"
            ).encode("ascii")
        )
    page.contents_add(pdf.make_stream(bytes(commands)))


def _box_dimensions(page: pikepdf.Page, box_name: str) -> BoxDimensions:
    coordinates = getattr(page, box_name.lower())
    left, bottom, right, top = (float(value) for value in coordinates)
    if right <= left or top <= bottom:
        raise ValueError(f"The selected {box_name} is invalid.")
    return BoxDimensions(box_name, left, bottom, right, top)


def _prepare_printer_page(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    box: BoxDimensions,
    geometry: OutputPageGeometry,
    layout: DotLayout,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
) -> None:
    output_width = geometry.width_mm * POINTS_PER_MM
    output_height = geometry.height_mm * POINTS_PER_MM
    offset_x = geometry.source_offset_x_mm * POINTS_PER_MM
    offset_y = geometry.source_offset_y_mm * POINTS_PER_MM

    _wrap_page_content_in_translation(
        pdf,
        page,
        offset_x - box.left,
        offset_y - box.bottom,
        box,
        repeat_columns,
        repeat_rows,
        repeat_gap_mm,
    )
    page.obj["/MediaBox"] = pikepdf.Array([0, 0, output_width, output_height])
    page.obj["/CropBox"] = pikepdf.Array([0, 0, output_width, output_height])
    page.obj["/BleedBox"] = pikepdf.Array([0, 0, output_width, output_height])
    page.obj["/TrimBox"] = pikepdf.Array([0, 0, output_width, output_height])
    page.obj["/ArtBox"] = pikepdf.Array([0, 0, output_width, output_height])
    _translate_annotations(
        pdf,
        page,
        offset_x - box.left,
        offset_y - box.bottom,
        box,
        repeat_columns,
        repeat_rows,
        repeat_gap_mm,
    )
    repeated_box = _repeated_box(
        box,
        repeat_columns,
        repeat_rows,
        repeat_gap_mm,
    )
    page.contents_add(
        pdf.make_stream(_dot_drawing_commands(repeated_box, layout, offset_x, offset_y))
    )


def _wrap_page_content_in_translation(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    translate_x: float,
    translate_y: float,
    clip_box: BoxDimensions,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
) -> None:
    existing = page.obj.get("/Contents")
    if existing is None:
        original_contents: list[pikepdf.Object] = []
    elif isinstance(existing, pikepdf.Array):
        original_contents = list(existing)
    else:
        original_contents = [existing]

    contents: list[pikepdf.Object] = []
    gap_points = repeat_gap_mm * POINTS_PER_MM
    for row in range(repeat_rows):
        for column in range(repeat_columns):
            prefix = pdf.make_stream(
                (
                    f"q 1 0 0 1 "
                    f"{_pdf_number(translate_x + column * (clip_box.width_points + gap_points))} "
                    f"{_pdf_number(translate_y + row * (clip_box.height_points + gap_points))} cm\n"
                    f"{_pdf_number(clip_box.left)} {_pdf_number(clip_box.bottom)} "
                    f"{_pdf_number(clip_box.width_points)} "
                    f"{_pdf_number(clip_box.height_points)} re W n\n"
                ).encode("ascii")
            )
            contents.extend(
                [prefix, *original_contents, pdf.make_stream(b"\nQ\n")]
            )
    page.obj["/Contents"] = pikepdf.Array(contents)


def _translate_annotations(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    translate_x: float,
    translate_y: float,
    box: BoxDimensions,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
) -> None:
    annotations = page.obj.get("/Annots")
    if annotations is None:
        return
    source_annotations = list(annotations)
    annotation_templates = [
        {key: value for key, value in annotation.items()}
        for annotation in source_annotations
    ]
    translated_annotations: list[pikepdf.Object] = []
    gap_points = repeat_gap_mm * POINTS_PER_MM
    for row in range(repeat_rows):
        for column in range(repeat_columns):
            tile_translate_x = translate_x + column * (box.width_points + gap_points)
            tile_translate_y = translate_y + row * (box.height_points + gap_points)
            for annotation_index, annotation in enumerate(source_annotations):
                if row == 0 and column == 0:
                    translated = annotation
                else:
                    translated = pdf.make_indirect(
                        pikepdf.Dictionary(annotation_templates[annotation_index])
                    )
                _translate_annotation(translated, tile_translate_x, tile_translate_y)
                translated_annotations.append(translated)
    page.obj["/Annots"] = pikepdf.Array(translated_annotations)


def _translate_annotation(
    annotation: pikepdf.Object,
    translate_x: float,
    translate_y: float,
) -> None:
    rectangle = annotation.get("/Rect")
    if rectangle is not None and len(rectangle) == 4:
        annotation["/Rect"] = pikepdf.Array(
            [
                float(rectangle[0]) + translate_x,
                float(rectangle[1]) + translate_y,
                float(rectangle[2]) + translate_x,
                float(rectangle[3]) + translate_y,
            ]
        )
    for key in ("/QuadPoints", "/Vertices", "/L", "/CL"):
        values = annotation.get(key)
        if values is not None:
            annotation[key] = _translate_coordinate_pairs(
                values,
                translate_x,
                translate_y,
            )
    ink_list = annotation.get("/InkList")
    if ink_list is not None:
        annotation["/InkList"] = pikepdf.Array(
            [
                _translate_coordinate_pairs(stroke, translate_x, translate_y)
                for stroke in ink_list
            ]
        )


def _translate_coordinate_pairs(
    values: pikepdf.Array,
    translate_x: float,
    translate_y: float,
) -> pikepdf.Array:
    translated = list(values)
    for index in range(0, len(translated) - 1, 2):
        translated[index] = float(translated[index]) + translate_x
        translated[index + 1] = float(translated[index + 1]) + translate_y
    return pikepdf.Array(translated)


def _create_cutter_page(
    pdf: pikepdf.Pdf,
    geometry: OutputPageGeometry,
    box: BoxDimensions,
    layout: DotLayout,
    cut_width_mm: float,
    cut_height_mm: float,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
) -> None:
    page = pdf.add_blank_page(
        page_size=(
            geometry.width_mm * POINTS_PER_MM,
            geometry.height_mm * POINTS_PER_MM,
        )
    )
    repeated_box = _repeated_box(
        box,
        repeat_columns,
        repeat_rows,
        repeat_gap_mm,
    )
    commands = _dot_drawing_commands(
        repeated_box,
        layout,
        geometry.source_offset_x_mm * POINTS_PER_MM,
        geometry.source_offset_y_mm * POINTS_PER_MM,
    )
    gap_points = repeat_gap_mm * POINTS_PER_MM
    cut_width_points = cut_width_mm * POINTS_PER_MM
    cut_height_points = cut_height_mm * POINTS_PER_MM
    for row in range(repeat_rows):
        for column in range(repeat_columns):
            cut_x = (
                geometry.source_offset_x_mm * POINTS_PER_MM
                + column * (box.width_points + gap_points)
                + (box.width_points - cut_width_points) / 2
            )
            cut_y = (
                geometry.source_offset_y_mm * POINTS_PER_MM
                + row * (box.height_points + gap_points)
                + (box.height_points - cut_height_points) / 2
            )
            commands += (
                f"0 0 0 RG 0.75 w {_pdf_number(cut_x)} {_pdf_number(cut_y)} "
                f"{_pdf_number(cut_width_points)} "
                f"{_pdf_number(cut_height_points)} re S\n"
            ).encode("ascii")
    page.contents_add(pdf.make_stream(commands))


def _repeated_box(
    box: BoxDimensions,
    repeat_columns: int,
    repeat_rows: int,
    repeat_gap_mm: float,
) -> BoxDimensions:
    gap_points = repeat_gap_mm * POINTS_PER_MM
    return BoxDimensions(
        name=box.name,
        left=0,
        bottom=0,
        right=repeat_columns * box.width_points + (repeat_columns - 1) * gap_points,
        top=repeat_rows * box.height_points + (repeat_rows - 1) * gap_points,
    )


def _dot_drawing_commands(
    box: BoxDimensions,
    layout: DotLayout,
    offset_x: float,
    offset_y: float,
) -> bytes:
    width_points = box.width_points
    height_points = box.height_points
    dot_radius = 3.0 * POINTS_PER_MM
    outward_offset = 8.0 * POINTS_PER_MM
    corner_offset = 3.0 * POINTS_PER_MM
    if layout == "left_right":
        centers = (
            (offset_x - outward_offset, offset_y + corner_offset),
            (offset_x + width_points + outward_offset, offset_y + corner_offset),
            (
                offset_x + width_points + outward_offset,
                offset_y + height_points - corner_offset,
            ),
            (offset_x - outward_offset, offset_y + height_points - corner_offset),
        )
    else:
        centers = (
            (offset_x + corner_offset, offset_y - outward_offset),
            (offset_x + corner_offset, offset_y + height_points + outward_offset),
            (
                offset_x + width_points - corner_offset,
                offset_y + height_points + outward_offset,
            ),
            (offset_x + width_points - corner_offset, offset_y - outward_offset),
        )
    commands = bytearray(b"0 0 0 rg\n")
    for center_x, center_y in centers:
        commands.extend(_circle_path(center_x, center_y, dot_radius))
    return bytes(commands)


def _circle_path(center_x: float, center_y: float, radius: float) -> bytes:
    control = radius * 0.5522847498
    x = _pdf_number
    return (
        f"{x(center_x + radius)} {x(center_y)} m "
        f"{x(center_x + radius)} {x(center_y + control)} "
        f"{x(center_x + control)} {x(center_y + radius)} "
        f"{x(center_x)} {x(center_y + radius)} c "
        f"{x(center_x - control)} {x(center_y + radius)} "
        f"{x(center_x - radius)} {x(center_y + control)} "
        f"{x(center_x - radius)} {x(center_y)} c "
        f"{x(center_x - radius)} {x(center_y - control)} "
        f"{x(center_x - control)} {x(center_y - radius)} "
        f"{x(center_x)} {x(center_y - radius)} c "
        f"{x(center_x + control)} {x(center_y - radius)} "
        f"{x(center_x + radius)} {x(center_y - control)} "
        f"{x(center_x + radius)} {x(center_y)} c h f\n"
    ).encode("ascii")


def _pdf_number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("PDF coordinates must be finite numbers.")
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _temporary_path(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".tmp.pdf",
        dir=destination.parent,
    )
    os.close(descriptor)
    Path(name).unlink()
    return Path(name)


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value.strip())
    safe = re.sub(r"\s+", "_", safe)
    safe = re.sub(r"_+", "_", safe)
    return safe.strip("._ ")


def _filename_dimension(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", "p")
