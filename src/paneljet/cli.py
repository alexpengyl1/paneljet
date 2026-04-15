from __future__ import annotations

import argparse
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
IGNORED_PATTERNS = (
    re.compile(r".*\.jsx$", re.IGNORECASE),
    re.compile(r".*_combined.*\.pdf$", re.IGNORECASE),
    re.compile(r".*layout.*\.ai$", re.IGNORECASE),
)

MM_PER_INCH = 25.4
PT_PER_INCH = 72.0


@dataclass(frozen=True)
class FigureSpec:
    path: Path
    label: str
    width: float
    height: float

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    @property
    def shape(self) -> str:
        if self.aspect_ratio >= 1.8:
            return "wide"
        if self.aspect_ratio <= 0.8:
            return "tall"
        return "standard"


def natural_key(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value.lower())
    key: list[object] = []
    for part in parts:
        key.append(int(part) if part.isdigit() else part)
    return key


def mm_to_pt(mm: float) -> float:
    return mm / MM_PER_INCH * PT_PER_INCH


def parse_artboard(spec: str, landscape: bool) -> tuple[float, float]:
    named = {
        "A4": (210.0, 297.0),
        "A3": (297.0, 420.0),
        "LETTER": (215.9, 279.4),
    }
    if "x" in spec.lower():
        width_mm, height_mm = spec.lower().split("x", 1)
        width_pt = mm_to_pt(float(width_mm))
        height_pt = mm_to_pt(float(height_mm))
    else:
        dims = named.get(spec.upper())
        if not dims:
            raise ValueError(f"Unsupported artboard size: {spec}")
        width_pt = mm_to_pt(dims[0])
        height_pt = mm_to_pt(dims[1])
    if landscape:
        width_pt, height_pt = max(width_pt, height_pt), min(width_pt, height_pt)
    return width_pt, height_pt


def should_ignore(path: Path) -> bool:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return True
    return any(pattern.match(path.name) for pattern in IGNORED_PATTERNS)


def discover_paths(folder: Path) -> list[Path]:
    files = [path for path in folder.iterdir() if path.is_file() and not should_ignore(path)]
    return sorted(files, key=lambda path: natural_key(path.name))


def read_lines_file(file_path: Path) -> list[str]:
    rows: list[str] = []
    for raw in file_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(line)
    return rows


def normalize_manual_entries(raw_items: Sequence[str]) -> list[tuple[str | None, str]]:
    entries: list[tuple[str | None, str]] = []
    for raw in raw_items:
        item = raw.strip()
        if not item:
            continue
        if "=" in item:
            maybe_label, maybe_name = item.split("=", 1)
            label = maybe_label.strip()
            name = maybe_name.strip()
            if not label or not name:
                raise ValueError(f"Invalid label=file entry: {raw}")
            entries.append((label, name))
        else:
            entries.append((None, item))
    return entries


def validate_label(label: str) -> str:
    cleaned = label.strip()
    if not cleaned:
        raise ValueError("Labels cannot be empty.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", cleaned):
        raise ValueError(f"Unsupported label {label!r}. Use letters/numbers/underscore/hyphen.")
    return cleaned


def label_for_index(index: int) -> str:
    letters = ""
    value = index
    while True:
        value, remainder = divmod(value, 26)
        letters = chr(ord("A") + remainder) + letters
        if value == 0:
            return letters
        value -= 1


def assign_default_labels(paths: Sequence[Path]) -> list[tuple[str, Path]]:
    return [(label_for_index(index), path) for index, path in enumerate(paths)]


def parse_manual_selection(
    folder: Path,
    files_argument: str | None,
    files_file: str | None,
) -> list[tuple[str, Path]] | None:
    raw_entries: list[str] = []
    if files_argument:
        raw_entries.extend([part.strip() for part in files_argument.split(",")])
    if files_file:
        raw_entries.extend(read_lines_file(Path(files_file).expanduser().resolve()))
    if not raw_entries:
        return None

    seen_labels: set[str] = set()
    seen_paths: set[Path] = set()
    parsed: list[tuple[str, Path]] = []
    next_auto_index = 0
    for maybe_label, name in normalize_manual_entries(raw_entries):
        path = (folder / name).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Requested file does not exist: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"Expected a file but got a directory: {path}")
        if should_ignore(path):
            raise ValueError(f"Requested file is not a supported input figure: {path.name}")
        label = validate_label(maybe_label) if maybe_label else label_for_index(next_auto_index)
        next_auto_index += 1
        if label in seen_labels:
            raise ValueError(f"Duplicate label in manual order: {label}")
        if path in seen_paths:
            raise ValueError(f"Duplicate file in manual order: {path.name}")
        seen_labels.add(label)
        seen_paths.add(path)
        parsed.append((label, path))
    return parsed


def read_dimensions(path: Path) -> tuple[float, float]:
    result = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    width = None
    height = None
    for line in result.stdout.splitlines():
        text = line.strip()
        if text.startswith("pixelWidth:"):
            width = float(text.split(":", 1)[1].strip())
        elif text.startswith("pixelHeight:"):
            height = float(text.split(":", 1)[1].strip())
    if width is None or height is None or width <= 0 or height <= 0:
        raise ValueError(f"Could not determine dimensions for {path}")
    return width, height


def build_figures(items: Sequence[tuple[str, Path]]) -> list[FigureSpec]:
    figures: list[FigureSpec] = []
    for label, path in items:
        width, height = read_dimensions(path)
        figures.append(FigureSpec(path=path, label=label, width=width, height=height))
    return figures


def chunk_sizes(total: int, preferred: int) -> list[int]:
    if total <= 0:
        return []
    rows: list[int] = []
    remaining = total
    while remaining > 0:
        size = min(preferred, remaining)
        if remaining == 4 and preferred == 3:
            size = 2
        rows.append(size)
        remaining -= size
    return rows


def smart_reorder(figures: Sequence[FigureSpec]) -> list[FigureSpec]:
    standard = [figure for figure in figures if figure.shape == "standard"]
    tall = [figure for figure in figures if figure.shape == "tall"]
    wide = [figure for figure in figures if figure.shape == "wide"]

    ordered: list[FigureSpec] = []
    ordered.extend(standard[:3])
    ordered.extend(tall[:3])
    ordered.extend(standard[3:])
    ordered.extend(tall[3:])
    ordered.extend(wide)
    return ordered


def balanced_layout(count: int) -> list[int]:
    if count <= 0:
        raise ValueError("No files to place.")
    if count <= 3:
        return [count]
    if count == 4:
        return [2, 2]
    cols = math.ceil(math.sqrt(count))
    rows_count = math.ceil(count / cols)
    rows: list[int] = []
    remaining = count
    for _ in range(rows_count):
        size = min(cols, remaining)
        if remaining == 4:
            size = 2
        rows.append(size)
        remaining -= size
    return rows


def smart_layout(figures: Sequence[FigureSpec]) -> list[int]:
    standard_count = sum(1 for figure in figures if figure.shape == "standard")
    tall_count = sum(1 for figure in figures if figure.shape == "tall")
    wide_count = sum(1 for figure in figures if figure.shape == "wide")
    shape_types = sum(1 for count in (standard_count, tall_count, wide_count) if count > 0)
    if shape_types <= 1:
        return balanced_layout(len(figures))

    rows: list[int] = []
    remaining_standard = standard_count
    remaining_tall = tall_count

    if remaining_standard >= 3 and (remaining_tall > 0 or wide_count > 0):
        rows.append(3)
        remaining_standard -= 3

    if remaining_tall >= 3:
        rows.append(3)
        remaining_tall -= 3

    remaining = len(figures) - sum(rows)
    if remaining == 4:
        rows.extend([2, 2])
    elif remaining > 0:
        rows.extend(chunk_sizes(remaining, 2 if wide_count else 3))

    if sum(rows) != len(figures):
        raise ValueError("Internal error while building smart layout.")
    return rows


def parse_layout(raw: str | None, count: int, mode: str, figures: Sequence[FigureSpec]) -> list[int]:
    if count <= 0:
        raise ValueError("No files to place.")
    if raw:
        rows = [int(part.strip()) for part in raw.split(",") if part.strip()]
        if sum(rows) != count:
            raise ValueError(f"Layout {raw!r} expects {sum(rows)} panels, but found {count}.")
        if any(value <= 0 for value in rows):
            raise ValueError("Layout values must be positive integers.")
        return rows
    if mode == "smart":
        return smart_layout(figures)
    return balanced_layout(count)


def chunked(values: Sequence[FigureSpec], sizes: Sequence[int]) -> list[list[FigureSpec]]:
    rows: list[list[FigureSpec]] = []
    start = 0
    for size in sizes:
        rows.append(list(values[start : start + size]))
        start += size
    return rows


def js_string(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def generate_jsx(
    figures: Sequence[FigureSpec],
    layout: Sequence[int],
    artboard_width: float,
    artboard_height: float,
    margin: float,
    gap: float,
    add_labels: bool,
    label_size: float,
    document_name: str,
    save_ai: Path | None,
) -> str:
    rows = chunked(figures, layout)
    row_count = len(rows)
    inner_width = artboard_width - (2 * margin)
    inner_height = artboard_height - (2 * margin)
    row_height = (inner_height - (gap * (row_count - 1))) / row_count
    if row_height <= 0 or inner_width <= 0:
        raise ValueError("Margin and gap leave no usable artboard space.")

    placements: list[dict[str, object]] = []
    for row_index, row_figures in enumerate(rows):
        row_top = artboard_height - margin - (row_index * (row_height + gap))
        cell_width = (inner_width - (gap * (len(row_figures) - 1))) / len(row_figures)
        for col_index, figure in enumerate(row_figures):
            cell_left = margin + (col_index * (cell_width + gap))
            placements.append(
                {
                    "label": figure.label,
                    "file": str(figure.path.resolve()),
                    "cell_left": round(cell_left, 2),
                    "cell_top": round(row_top, 2),
                    "cell_width": round(cell_width, 2),
                    "cell_height": round(row_height, 2),
                }
            )

    placement_lines = []
    for item in placements:
        placement_lines.append(
            "  {label: \"%s\", file: \"%s\", cellLeft: %.2f, cellTop: %.2f, cellWidth: %.2f, cellHeight: %.2f}"
            % (
                js_string(str(item["label"])),
                js_string(str(item["file"])),
                item["cell_left"],
                item["cell_top"],
                item["cell_width"],
                item["cell_height"],
            )
        )

    save_block = "var saveAIPath = null;"
    if save_ai:
        save_block = 'var saveAIPath = "%s";' % js_string(str(save_ai.resolve()))

    return f"""#target illustrator

var docWidth = {artboard_width:.2f};
var docHeight = {artboard_height:.2f};
var labelSize = {label_size:.2f};
var documentTitle = "{js_string(document_name)}";
var addLabels = {"true" if add_labels else "false"};
{save_block}

var placements = [
{",\n".join(placement_lines)}
];

function ensureDocument() {{
  var doc = app.documents.add(DocumentColorSpace.RGB, docWidth, docHeight);
  doc.rulerUnits = RulerUnits.Points;
  return doc;
}}

function placeOne(doc, spec) {{
  var fileRef = new File(spec.file);
  if (!fileRef.exists) {{
    throw new Error("Missing file: " + spec.file);
  }}

  var item = doc.placedItems.add();
  item.file = fileRef;

  var scaleX = spec.cellWidth / item.width;
  var scaleY = spec.cellHeight / item.height;
  var scale = Math.min(scaleX, scaleY) * 100.0;
  item.resize(scale, scale);

  var left = spec.cellLeft + (spec.cellWidth - item.width) / 2.0;
  var top = spec.cellTop - (spec.cellHeight - item.height) / 2.0;
  item.left = left;
  item.top = top;

  if (addLabels) {{
    var label = doc.textFrames.add();
    label.contents = spec.label;
    label.left = spec.cellLeft;
    label.top = spec.cellTop + (labelSize * 0.2);
    label.textRange.characterAttributes.size = labelSize;
  }}

  return item;
}}

function main() {{
  var doc = ensureDocument();
  for (var i = 0; i < placements.length; i++) {{
    placeOne(doc, placements[i]);
  }}
  if (saveAIPath) {{
    var saveFile = new File(saveAIPath);
    var options = new IllustratorSaveOptions();
    doc.saveAs(saveFile, options);
  }}
}}

main();
"""


def detect_illustrator_app(preferred: str | None) -> str:
    if preferred:
        return preferred
    return "Adobe Illustrator"


def run_illustrator(jsx_path: Path, app_name: str) -> None:
    subprocess.run(
        [
            "osascript",
            "-e",
            f'tell application "{app_name}"',
            "-e",
            f'set f to POSIX file "{jsx_path}" as alias',
            "-e",
            "activate",
            "-e",
            "do javascript f",
            "-e",
            "end tell",
        ],
        check=True,
    )


def print_summary(figures: Sequence[FigureSpec], layout: Sequence[int], mode: str, output_jsx: Path, save_ai: Path | None) -> None:
    print("Files:")
    for figure in figures:
        print(
            f"  {figure.label}: {figure.path.name}  "
            f"({figure.width:.0f}x{figure.height:.0f}, aspect={figure.aspect_ratio:.2f}, shape={figure.shape})"
        )
    print(f"Layout: {','.join(str(value) for value in layout)}")
    print(f"Layout mode: {mode}")
    print(f"Generated JSX: {output_jsx}")
    if save_ai:
        print(f"Target AI: {save_ai}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PanelJet: generate Adobe Illustrator JSX that packs a figure folder into an editable layout."
    )
    parser.add_argument("folder", help="Folder containing figure files.")
    parser.add_argument(
        "--files",
        help="Comma-separated explicit order. Supports 'file1.pdf,file2.pdf' or 'A=file1.pdf,B=file2.pdf'.",
    )
    parser.add_argument(
        "--files-file",
        help="Text file containing one filename or label=filename per line.",
    )
    parser.add_argument(
        "--order-mode",
        choices=("natural", "smart"),
        default="natural",
        help="Natural filename order or smart shape-aware reordering. Default: natural.",
    )
    parser.add_argument(
        "--layout",
        help="Panels per row, for example 3,3,2. Overrides automatic layout selection.",
    )
    parser.add_argument(
        "--layout-mode",
        choices=("balanced", "smart"),
        default="balanced",
        help="Automatic layout strategy when --layout is omitted. Default: balanced.",
    )
    parser.add_argument(
        "--output-jsx",
        help="Path to write the generated JSX. Defaults to <folder>/paneljet.jsx.",
    )
    parser.add_argument(
        "--name",
        help="Base output name. If set, defaults become <folder>/<name>.jsx and optionally <folder>/<name>.ai.",
    )
    parser.add_argument(
        "--save-ai",
        help="Optional .ai path that the JSX should save after placing panels.",
    )
    parser.add_argument(
        "--artboard-size",
        default="A4",
        help="A4, A3, letter, or WxH in mm. Default: A4.",
    )
    parser.add_argument(
        "--ai-width-mm",
        type=float,
        help="Explicit artboard width in mm. Must be used together with --ai-height-mm.",
    )
    parser.add_argument(
        "--ai-height-mm",
        type=float,
        help="Explicit artboard height in mm. Must be used together with --ai-width-mm.",
    )
    parser.add_argument("--landscape", action="store_true", help="Use landscape orientation.")
    parser.add_argument("--margin", type=float, default=24.0, help="Outer margin in points.")
    parser.add_argument("--gap", type=float, default=16.0, help="Gap between cells in points.")
    parser.add_argument("--label-size", type=float, default=18.0, help="Panel label size in points.")
    parser.add_argument("--no-labels", action="store_true", help="Do not add labels.")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned order/layout without writing files.")
    parser.add_argument(
        "--run-illustrator",
        action="store_true",
        help="Ask Illustrator to run the generated JSX immediately.",
    )
    parser.add_argument(
        "--illustrator-app",
        help="Illustrator app name for AppleScript, for example 'Adobe Illustrator'.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        parser.error(f"Folder does not exist: {folder}")
    if args.run_illustrator and args.dry_run:
        parser.error("--run-illustrator cannot be used together with --dry-run.")
    if (args.ai_width_mm is None) != (args.ai_height_mm is None):
        parser.error("--ai-width-mm and --ai-height-mm must be used together.")
    if args.name and ("/" in args.name or "\\" in args.name):
        parser.error("--name should be a base filename, not a path.")

    manual_items = parse_manual_selection(folder, args.files, args.files_file)
    if manual_items is not None:
        labeled_paths = manual_items
    else:
        labeled_paths = assign_default_labels(discover_paths(folder))

    if not labeled_paths:
        parser.error(f"No supported figure files found in {folder}")

    figures = build_figures(labeled_paths)
    if manual_items is None and args.order_mode == "smart":
        figures = smart_reorder(figures)
        figures = [
            FigureSpec(path=figure.path, label=label_for_index(index), width=figure.width, height=figure.height)
            for index, figure in enumerate(figures)
        ]

    layout = parse_layout(args.layout, len(figures), args.layout_mode, figures)
    if args.ai_width_mm is not None and args.ai_height_mm is not None:
        artboard_width = mm_to_pt(args.ai_width_mm)
        artboard_height = mm_to_pt(args.ai_height_mm)
    else:
        artboard_width, artboard_height = parse_artboard(args.artboard_size, args.landscape)

    default_base = args.name if args.name else "paneljet"
    output_jsx = (
        Path(args.output_jsx).expanduser().resolve()
        if args.output_jsx
        else (folder / f"{default_base}.jsx").resolve()
    )
    if args.save_ai:
        save_ai = Path(args.save_ai).expanduser().resolve()
    elif args.name:
        save_ai = (folder / f"{default_base}.ai").resolve()
    else:
        save_ai = None

    print(f"Scanned folder: {folder}")
    print_summary(figures, layout, args.layout_mode if not args.layout else "manual", output_jsx, save_ai)
    print(
        "Artboard: "
        f"{artboard_width:.2f} pt x {artboard_height:.2f} pt "
        f"({artboard_width / PT_PER_INCH * MM_PER_INCH:.1f} mm x {artboard_height / PT_PER_INCH * MM_PER_INCH:.1f} mm)"
    )

    if args.dry_run:
        return 0

    output_jsx.parent.mkdir(parents=True, exist_ok=True)
    if save_ai:
        save_ai.parent.mkdir(parents=True, exist_ok=True)

    jsx = generate_jsx(
        figures=figures,
        layout=layout,
        artboard_width=artboard_width,
        artboard_height=artboard_height,
        margin=args.margin,
        gap=args.gap,
        add_labels=not args.no_labels,
        label_size=args.label_size,
        document_name=folder.name,
        save_ai=save_ai,
    )
    output_jsx.write_text(jsx, encoding="utf-8")

    if args.run_illustrator:
        app_name = detect_illustrator_app(args.illustrator_app)
        print(f"Running Illustrator via: {app_name}")
        run_illustrator(output_jsx, app_name)
        print("Illustrator execution requested successfully.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
