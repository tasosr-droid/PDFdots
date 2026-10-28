# PDF Dots

A Windows desktop application for preparing a printer PDF and a cutter PDF from
an input PDF.

## Ground rules

- The printer output contains the source artwork and four added registration
  dots. Do not rasterize, flatten, recolor, scale, or otherwise edit the
  artwork.
- Keep the source artwork operators intact and add the registration dots as
  separate vector content. The page content is translated without scaling to
  move the selected source box onto the enlarged output sheet and clipped to
  the chosen source box.
- CropBox is the default source size, but the user can select MediaBox,
  CropBox, BleedBox, TrimBox, or ArtBox. Selection can apply to the whole
  document or be chosen separately for each page.
- The cutter rectangle matches the selected source box by default. Optional
  fake bleed applies a uniform inset to that rectangle; the default is 2.5 mm
  per side, which turns a 215 x 302 mm source into a 210 x 297 mm cutline.
- Safety distance defaults to 1 mm. Both it and the fake-bleed inset are
  adjustable in the UI.
- The dot layout uses 6 mm dots, 8 mm offsets beyond the selected
  source box on the chosen pair of sides, and 3 mm offsets from the other two
  edges. The expanded output size is calculated from the dot bounds plus the
  safety distance. Thus the A4 left/right example produces a 234 x 299 mm
  output at the default 1 mm safety distance.
- Repeated layouts can place each selected source page in a horizontal and
  vertical grid. The default is 1 x 1; the gap between repeated pages defaults
  to 5 mm. Each output page contains the configured number of source-page
  copies, one cut rectangle per copy, and only four registration dots around
  the outside of the complete grid.
- Alternatively, enable document-page arrangement to place consecutive pages
  from a multipage PDF into the grid row by row. This groups pages into output
  layouts (for example, an 8-page PDF in a 2 x 2 grid becomes two layouts).
  The final layout keeps its full grid size and leaves unused slots blank.
- The printer and cutter outputs retain every source page's content. In
  document-page arrangement mode, groups of source pages are composed into
  output layouts; otherwise, each source page gets its own output page. If
  output page sizes vary, the filenames use `variable-size`.
- Pages with a non-zero PDF rotation are currently rejected with an explicit
  error; save an upright copy of the PDF before generating files.
- PDF page boxes are read using their effective values. Page box dimensions
  are displayed in millimetres.
- Saving a modified PDF necessarily changes its PDF container and page
  structure. “Untouched artwork” means the artwork is not rendered and
  reconstructed or deliberately modified; it cannot mean that the complete
  output file is byte-for-byte identical to the input.
- Dot position values are currently fixed to the agreed example. Making those
  offsets configurable can be added separately if needed.

## Technology

- **PySide6** provides the Windows desktop UI.
- **pikepdf** (QPDF) handles PDF pages, boxes, and preservation-oriented
  composition, including the generated vector marks and cutter artwork.

The UI can inspect PDFs, preview the output layout, and generate a
printer/cutter PDF pair. Drop a PDF onto the source field or anywhere in the
main window to open it. The interface shows page dimensions alongside the
controls; its layout preview is a schematic of the output sheet, source area,
registration dots, and cutter rectangle (it does not render the source
artwork). The printer output preserves the original page content and adds
dots; the cutter output contains only the dots and cut rectangle.

Version 0.3 added the two-column dimensions view, PDF drag and drop, and a
separate layout-preview popup before generation. Version 0.4 adds configurable
horizontal and vertical page repetition, document-page arrangement, and an
adjustable gap.

## Setup

Install Python 3.10 or newer, then from this directory run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pdfdots
```

## Build a distributable Windows folder

On Windows, install the app and its build tools, then run the packaging script:

```powershell
python -m pip install -e ".[build]"
.\build_windows.ps1
```

The distributable application is written to `dist\PDFDots\`. Copy the entire
folder to another Windows computer and launch `PDFDots.exe` inside it; Python
and the Python packages do not need to be installed. The onedir build avoids
extracting the bundled runtime on every launch, so startup is faster than the
single-file build. The executable and application window use the `dots.png`
artwork as their icon.

## Current status

The UI supports choosing a PDF and output folder, inspecting page boxes,
selecting a source box for the whole document or per page, adjusting safety
distance and optional fake bleed, and configuring repeated page layouts. It
writes the two output PDFs with the selected source box, dot layout, repeat
grid, and size reflected in their names.
