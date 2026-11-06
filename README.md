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
  the chosen source box, or its true-bleed expansion when enabled.
- CropBox is the default source size, but the user can select MediaBox,
  CropBox, BleedBox, TrimBox, or ArtBox. Selection can apply to the whole
  document or be chosen separately for each page.
- The cutter rectangle matches the selected source box by default. Optional
  fake bleed applies a uniform inset to that rectangle; the default is 2.5 mm
  per side, which turns a 215 x 302 mm source into a 210 x 297 mm cutline.
- Optional true bleed expands the printed artwork beyond the selected source
  box while leaving the cutter rectangle at the selected box. The default is
  2 mm per side and requires that much existing artwork outside the selected
  box within the page's MediaBox. A 210 x 297 mm cut box with 2 mm of true
  bleed prints a 214 x 301 mm image.
- The repeat gap is measured between cutter rectangles. True bleed extends
  into that gap: with a 5 mm gap and 2 mm of bleed per side, the printed
  images have 1 mm between them. The app rejects settings that would cause
  neighboring printed areas to overlap.
- Safety distance defaults to 1 mm. Safety, fake-bleed inset, and true-bleed
  amount are adjustable in the UI.
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
- A third mode puts selected copies of each document page in one fixed grid.
  Set a copy count per page; copies are placed in page order, left to right and
  top to bottom. For example, copy counts of 2, 2, and 4 place two copies each
  of pages 1 and 2, followed by four copies of page 3. Unused grid slots stay
  blank. Copy counts must fit the configured row and column capacity.
- The printer and cutter outputs retain every source page's content. In
  document-page arrangement mode, groups of source pages are composed into
  output layouts; in per-page-copy mode, the selected page copies are composed
  into one output sheet. Otherwise, each source page gets its own output page.
  If output page sizes vary, the filenames use `variable-size`.
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

The UI can inspect PDFs, preview the output layout live as settings change,
and generate a printer/cutter PDF pair. Repeat counts, gaps, document-page arrangement, and per-page copy counts are
controlled directly in the main
window. Open the live preview in a separate, full-screen-capable window for
closer inspection. Drop a PDF onto the
source field or anywhere in the main window to open it. The live preview shows
the output sheet, source areas, page numbers, registration dots, and cutter
rectangles. The actual-page preview crops artwork to the selected PDF page box,
including artwork outside the CropBox when a larger box such as BleedBox is
selected. The printer output preserves the original page content and adds dots;
the cutter output contains only the dots and cut rectangles.

Version 0.3 added the two-column dimensions view and PDF drag and drop.
Version 0.4 added configurable horizontal and vertical page repetition,
document-page arrangement, and an adjustable gap. Version 0.5 adds an
always-visible live preview, inline repeat controls, optional rendered page
artwork, per-page copy counts in a fixed document grid, and an expanded
full-screen preview window.

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
distance, fake bleed, and true bleed, and configuring repeated page layouts.
Live preview updates show the source bounds and cutter rectangle alongside
the dots and final sheet size. It writes the two output PDFs with the selected
source box, dot layout, repeat grid, and size reflected in their names.
