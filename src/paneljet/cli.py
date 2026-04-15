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


@dataclass(frozen=True)
class PlacementSpec:
    label: str
    file: str
    cell_left: float
    cell_top: float
    cell_width: float
    cell_height: float
    show_label: bool = True


@dataclass(frozen=True)
class GroupSpec:
    name: str
    figures: list[FigureSpec]
    layout: list[int]


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


def parse_layout_string(raw: str, expected_count: int | None = None) -> list[int]:
    rows = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not rows:
        raise ValueError("Layout cannot be empty.")
    if any(value <= 0 for value in rows):
        raise ValueError("Layout values must be positive integers.")
    if expected_count is not None and sum(rows) != expected_count:
        raise ValueError(f"Layout {raw!r} expects {sum(rows)} panels, but found {expected_count}.")
    return rows


def parse_group_layout_file(folder: Path, file_path: Path) -> tuple[list[GroupSpec], list[list[str]]]:
    current_section: tuple[str, str] | None = None
    group_defs: dict[str, dict[str, str]] = {}
    figure_defs: dict[str, str] = {}

    for line in read_lines_file(file_path):
        if line.startswith("[") and line.endswith("]"):
            raw_section = line[1:-1].strip()
            if raw_section.lower() == "figure":
                current_section = ("figure", "figure")
                continue
            if raw_section.lower().startswith("group "):
                group_name = raw_section[6:].strip()
                if not group_name:
                    raise ValueError("Group section names cannot be empty.")
                validate_label(group_name)
                current_section = ("group", group_name)
                group_defs.setdefault(group_name, {})
                continue
            raise ValueError(f"Unsupported section header: {line}")

        if current_section is None:
            raise ValueError("Layout file must start with a [group NAME] or [figure] section.")
        if "=" not in line:
            raise ValueError(f"Expected key=value line, got: {line}")
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if not value:
            raise ValueError(f"Value cannot be empty for key {key!r}.")
        if current_section[0] == "group":
            group_defs[current_section[1]][key] = value
        else:
            figure_defs[key] = value

    if not group_defs:
        raise ValueError("No groups were defined in the group layout file.")
    rows_spec = figure_defs.get("rows")
    if not rows_spec:
        raise ValueError("The [figure] section must define rows = ...")

    groups: list[GroupSpec] = []
    group_names = set(group_defs)
    for name, values in group_defs.items():
        files_value = values.get("files")
        layout_value = values.get("layout")
        if not files_value or not layout_value:
            raise ValueError(f"Group {name} must define both files and layout.")
        file_names = [part.strip() for part in files_value.split(",") if part.strip()]
        if not file_names:
            raise ValueError(f"Group {name} must list at least one file.")
        items = parse_manual_selection(folder, ",".join(file_names), None)
        if items is None:
            raise ValueError(f"Group {name} could not be parsed.")
        figures = build_figures(items)
        groups.append(GroupSpec(name=name, figures=figures, layout=parse_layout_string(layout_value, len(figures))))

    outer_rows: list[list[str]] = []
    seen_groups: set[str] = set()
    for raw_row in rows_spec.split(";"):
        row = [part.strip() for part in raw_row.split(",") if part.strip()]
        if not row:
            continue
        for group_name in row:
            if group_name not in group_names:
                raise ValueError(f"Outer layout references unknown group {group_name!r}.")
            if group_name in seen_groups:
                raise ValueError(f"Group {group_name!r} appears more than once in figure rows.")
            seen_groups.add(group_name)
        outer_rows.append(row)

    missing = group_names - seen_groups
    if missing:
        raise ValueError(f"Groups missing from figure rows: {', '.join(sorted(missing))}")

    return groups, outer_rows


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


def candidate_layouts(count: int, max_row_size: int) -> list[list[int]]:
    if count <= 0:
        return []

    layouts: list[list[int]] = []

    def build(remaining: int, limit: int, current: list[int]) -> None:
        if remaining == 0:
            layouts.append(list(current))
            return
        upper = min(remaining, limit, max_row_size)
        for size in range(upper, 0, -1):
            current.append(size)
            build(remaining - size, size, current)
            current.pop()

    build(count, count, [])
    return layouts


def portrait_layout_score(figures: Sequence[FigureSpec], layout: Sequence[int]) -> float:
    rows = chunked(figures, layout)
    width_units = max(layout)
    height_units = sum(max(1.0 / figure.aspect_ratio for figure in row) for row in rows)
    layout_aspect = width_units / height_units
    target_aspect = 210.0 / 297.0
    single_panel_penalty = 0.12 * sum(1 for size in layout if size == 1)
    extra_row_penalty = 0.03 * max(0, len(layout) - 1)
    return abs(layout_aspect - target_aspect) + single_panel_penalty + extra_row_penalty


def improve_incomplete_layout(figures: Sequence[FigureSpec], layout: Sequence[int]) -> list[int]:
    if len(layout) <= 1 or layout[-1] == max(layout):
        return list(layout)

    best_layout = list(layout)
    best_score = portrait_layout_score(figures, layout)
    max_row_size = max(layout)
    for candidate in candidate_layouts(len(figures), max_row_size):
        if candidate == list(layout):
            continue
        candidate_score = portrait_layout_score(figures, candidate)
        if candidate_score + 0.05 < best_score:
            best_layout = candidate
            best_score = candidate_score
    return best_layout


def smart_layout(figures: Sequence[FigureSpec]) -> list[int]:
    standard_count = sum(1 for figure in figures if figure.shape == "standard")
    tall_count = sum(1 for figure in figures if figure.shape == "tall")
    wide_count = sum(1 for figure in figures if figure.shape == "wide")
    shape_types = sum(1 for count in (standard_count, tall_count, wide_count) if count > 0)
    if shape_types <= 1:
        return improve_incomplete_layout(figures, balanced_layout(len(figures)))

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
    return improve_incomplete_layout(figures, rows)


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


def compute_uniform_row_metrics(
    figures: Sequence[FigureSpec],
    layout: Sequence[int],
    artboard_width: float,
    margin: float,
    gap: float,
) -> tuple[float, list[float], list[list[FigureSpec]]]:
    rows = chunked(figures, layout)
    inner_width = artboard_width - (2 * margin)
    max_cols = max(layout)
    if inner_width <= 0:
        raise ValueError("Margin and gap leave no usable artboard space.")
    base_cell_width = (inner_width - (gap * (max_cols - 1))) / max_cols
    if base_cell_width <= 0:
        raise ValueError("Margin and gap leave no usable artboard space.")

    row_heights = [
        max(base_cell_width / figure.aspect_ratio for figure in row_figures)
        for row_figures in rows
    ]
    return base_cell_width, row_heights, rows


def layout_height_factor(figures: Sequence[FigureSpec], layout: Sequence[int]) -> float:
    rows = chunked(figures, layout)
    return sum(max(1.0 / figure.aspect_ratio for figure in row_figures) for row_figures in rows)


def group_aspect_ratio(group: GroupSpec, gap: float) -> float:
    height_factor = layout_height_factor(group.figures, group.layout)
    cols = max(group.layout)
    rows = len(group.layout)
    width = cols + (gap * max(0, cols - 1))
    height = height_factor + (gap * max(0, rows - 1))
    if height <= 0:
        raise ValueError(f"Group {group.name} has invalid geometry.")
    return width / height


def scaled_group_placements(
    group: GroupSpec,
    left: float,
    top: float,
    block_height: float,
    gap: float,
) -> list[PlacementSpec]:
    rows = chunked(group.figures, group.layout)
    row_count = len(rows)
    if row_count <= 0:
        return []
    available_content_height = block_height - (gap * max(0, row_count - 1))
    if available_content_height <= 0:
        raise ValueError(f"Group {group.name} has no vertical space after gaps.")

    height_factor = layout_height_factor(group.figures, group.layout)
    cell_width = available_content_height / height_factor
    row_heights = [
        max(cell_width / figure.aspect_ratio for figure in row_figures)
        for row_figures in rows
    ]
    max_cols = max(group.layout)
    total_width = (max_cols * cell_width) + (gap * max(0, max_cols - 1))
    placements: list[PlacementSpec] = []
    current_top = top
    for row_figures, row_height in zip(rows, row_heights):
        row_width = (len(row_figures) * cell_width) + (gap * max(0, len(row_figures) - 1))
        row_left = left + ((total_width - row_width) / 2.0)
        for col_index, figure in enumerate(row_figures):
            figure_height = cell_width / figure.aspect_ratio
            cell_left = row_left + (col_index * (cell_width + gap))
            placements.append(
                PlacementSpec(
                    label=figure.label,
                    file=str(figure.path.resolve()),
                    cell_left=round(cell_left, 2),
                    cell_top=round(current_top, 2),
                    cell_width=round(cell_width, 2),
                    cell_height=round(figure_height, 2),
                    show_label=False,
                )
            )
        current_top -= row_height + gap
    return placements


def compute_group_row_heights(
    groups: Sequence[GroupSpec],
    outer_rows: Sequence[Sequence[str]],
    artboard_width: float,
    margin: float,
    gap: float,
) -> tuple[list[float], dict[str, float]]:
    inner_width = artboard_width - (2 * margin)
    if inner_width <= 0:
        raise ValueError("Margin leaves no usable artboard width.")
    group_map = {group.name: group for group in groups}
    row_heights: list[float] = []
    aspects: dict[str, float] = {}
    for group in groups:
        aspects[group.name] = group_aspect_ratio(group, gap)
    for row in outer_rows:
        aspect_sum = sum(aspects[name] for name in row)
        available_width = inner_width - (gap * max(0, len(row) - 1))
        if available_width <= 0:
            raise ValueError("Gap leaves no usable row width.")
        row_heights.append(available_width / aspect_sum)
        for name in row:
            if name not in group_map:
                raise ValueError(f"Unknown group in row: {name}")
    return row_heights, aspects


def auto_height_for_groups(
    groups: Sequence[GroupSpec],
    outer_rows: Sequence[Sequence[str]],
    artboard_width: float,
    margin: float,
    gap: float,
) -> float:
    row_heights, _ = compute_group_row_heights(groups, outer_rows, artboard_width, margin, gap)
    return sum(row_heights) + (gap * max(0, len(row_heights) - 1)) + (2 * margin)


def group_label_placements(
    groups: Sequence[GroupSpec],
    outer_rows: Sequence[Sequence[str]],
    artboard_width: float,
    artboard_height: float,
    margin: float,
    gap: float,
) -> list[PlacementSpec]:
    row_heights, aspects = compute_group_row_heights(groups, outer_rows, artboard_width, margin, gap)
    inner_height = artboard_height - (2 * margin)
    content_height = sum(row_heights) + (gap * max(0, len(row_heights) - 1))
    group_map = {group.name: group for group in groups}
    _ = group_map
    placements: list[PlacementSpec] = []
    current_top = artboard_height - margin - ((inner_height - content_height) / 2.0)
    for row, row_height in zip(outer_rows, row_heights):
        current_left = margin
        for group_name in row:
            group_width = row_height * aspects[group_name]
            placements.append(
                PlacementSpec(
                    label=group_name,
                    file="",
                    cell_left=round(current_left, 2),
                    cell_top=round(current_top, 2),
                    cell_width=0.0,
                    cell_height=0.0,
                    show_label=True,
                )
            )
            current_left += group_width + gap
        current_top -= row_height + gap
    return placements


def auto_height_for_layout(
    figures: Sequence[FigureSpec],
    layout: Sequence[int],
    artboard_width: float,
    margin: float,
    gap: float,
) -> float:
    _, row_heights, rows = compute_uniform_row_metrics(figures, layout, artboard_width, margin, gap)
    content_height = sum(row_heights) + (gap * max(0, len(rows) - 1))
    return content_height + (2 * margin)


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
    base_cell_width, row_heights, rows = compute_uniform_row_metrics(
        figures, layout, artboard_width, margin, gap
    )
    row_count = len(rows)
    inner_height = artboard_height - (2 * margin)
    inner_width = artboard_width - (2 * margin)
    available_content_height = inner_height - (gap * (row_count - 1))
    if available_content_height <= 0:
        raise ValueError("Margin and gap leave no usable artboard space.")

    total_row_height = sum(row_heights)
    if total_row_height <= 0:
        raise ValueError("No usable row height could be computed.")

    if total_row_height > available_content_height:
        scale = available_content_height / total_row_height
        base_cell_width *= scale
        row_heights = [height * scale for height in row_heights]

    content_height = sum(row_heights) + (gap * (row_count - 1))
    content_top = artboard_height - margin - ((inner_height - content_height) / 2.0)

    placements: list[dict[str, object]] = []
    current_top = content_top
    for row_index, row_figures in enumerate(rows):
        row_height = row_heights[row_index]
        row_width = (base_cell_width * len(row_figures)) + (gap * (len(row_figures) - 1))
        row_left = margin + ((inner_width - row_width) / 2.0)
        for col_index, figure in enumerate(row_figures):
            figure_height = base_cell_width / figure.aspect_ratio
            if total_row_height > available_content_height:
                figure_height *= scale
            cell_left = row_left + (col_index * (base_cell_width + gap))
            placements.append(
                {
                    "label": figure.label,
                    "file": str(figure.path.resolve()),
                    "cell_left": round(cell_left, 2),
                    "cell_top": round(current_top, 2),
                    "cell_width": round(base_cell_width, 2),
                    "cell_height": round(figure_height, 2),
                }
            )
        current_top -= row_height + gap

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

    placements_block = ",\n".join(placement_lines)

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
{placements_block}
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


def render_jsx(
    placements: Sequence[PlacementSpec],
    artboard_width: float,
    artboard_height: float,
    add_labels: bool,
    label_size: float,
    document_name: str,
    save_ai: Path | None,
) -> str:
    placement_lines = []
    for item in placements:
        placement_lines.append(
            "  {label: \"%s\", file: \"%s\", cellLeft: %.2f, cellTop: %.2f, cellWidth: %.2f, cellHeight: %.2f, showLabel: %s}"
            % (
                js_string(item.label),
                js_string(item.file),
                item.cell_left,
                item.cell_top,
                item.cell_width,
                item.cell_height,
                "true" if item.show_label else "false",
            )
        )

    placements_block = ",\n".join(placement_lines)
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
{placements_block}
];

function ensureDocument() {{
  var doc = app.documents.add(DocumentColorSpace.RGB, docWidth, docHeight);
  doc.rulerUnits = RulerUnits.Points;
  return doc;
}}

function placeOne(doc, spec) {{
  if (spec.file) {{
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
  }}

  if (addLabels && spec.showLabel) {{
    var label = doc.textFrames.add();
    label.contents = spec.label;
    label.left = spec.cellLeft;
    label.top = spec.cellTop + (labelSize * 0.2);
    label.textRange.characterAttributes.size = labelSize;
  }}
  return null;
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


def generate_grouped_jsx(
    groups: Sequence[GroupSpec],
    outer_rows: Sequence[Sequence[str]],
    artboard_width: float,
    artboard_height: float,
    margin: float,
    gap: float,
    add_labels: bool,
    label_size: float,
    document_name: str,
    save_ai: Path | None,
) -> str:
    row_heights, aspects = compute_group_row_heights(groups, outer_rows, artboard_width, margin, gap)
    inner_height = artboard_height - (2 * margin)
    content_height = sum(row_heights) + (gap * max(0, len(row_heights) - 1))
    if content_height > inner_height:
        raise ValueError("Composite layout does not fit the requested artboard height.")

    group_map = {group.name: group for group in groups}
    placements: list[PlacementSpec] = []
    current_top = artboard_height - margin - ((inner_height - content_height) / 2.0)
    current_left_base = margin
    for row, row_height in zip(outer_rows, row_heights):
        current_left = current_left_base
        for group_name in row:
            group_width = row_height * aspects[group_name]
            placements.extend(
                scaled_group_placements(
                    group=group_map[group_name],
                    left=current_left,
                    top=current_top,
                    block_height=row_height,
                    gap=gap,
                )
            )
            current_left += group_width + gap
        current_top -= row_height + gap

    placements.extend(
        group_label_placements(
            groups=groups,
            outer_rows=outer_rows,
            artboard_width=artboard_width,
            artboard_height=artboard_height,
            margin=margin,
            gap=gap,
        )
    )

    return render_jsx(
        placements=placements,
        artboard_width=artboard_width,
        artboard_height=artboard_height,
        add_labels=add_labels,
        label_size=label_size,
        document_name=document_name,
        save_ai=save_ai,
    )


def detect_illustrator_app(preferred: str | None) -> str:
    if preferred:
        return preferred
    for candidate in ("Illustrator", "Adobe Illustrator"):
        result = subprocess.run(
            ["osascript", "-e", f'tell application "{candidate}" to get version'],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return candidate
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


def print_group_summary(
    groups: Sequence[GroupSpec],
    outer_rows: Sequence[Sequence[str]],
    output_jsx: Path,
    save_ai: Path | None,
) -> None:
    print("Groups:")
    for group in groups:
        file_summary = ", ".join(figure.path.name for figure in group.figures)
        print(f"  {group.name}: layout {','.join(str(value) for value in group.layout)} -> {file_summary}")
    print("Outer rows:")
    for row in outer_rows:
        print(f"  {' | '.join(row)}")
    print("Layout mode: composite")
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
        "--group-layout-file",
        help="Path to a grouped composite layout file with [group NAME] and [figure] rows definitions.",
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
    parser.add_argument(
        "--auto-height",
        action="store_true",
        help="Keep artboard width fixed and shrink artboard height to fit the packed panels.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the planned order/layout without writing files.")
    parser.add_argument(
        "--run-illustrator",
        action="store_true",
        help="Ask Illustrator to run the generated JSX immediately.",
    )
    parser.add_argument(
        "--illustrator-app",
        help="Illustrator app name for AppleScript, for example 'Illustrator' or 'Adobe Illustrator'.",
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
    if args.ai_height_mm is not None and args.ai_width_mm is None:
        parser.error("--ai-height-mm requires --ai-width-mm.")
    if args.ai_width_mm is not None and args.ai_height_mm is None and not args.auto_height:
        parser.error("--ai-width-mm and --ai-height-mm must be used together unless --auto-height is used.")
    if args.auto_height and args.ai_height_mm is not None:
        parser.error("--auto-height cannot be used together with --ai-height-mm.")
    if args.name and ("/" in args.name or "\\" in args.name):
        parser.error("--name should be a base filename, not a path.")
    if args.group_layout_file and (args.files or args.files_file or args.layout):
        parser.error("--group-layout-file cannot be combined with --files, --files-file, or --layout.")

    grouped_groups: list[GroupSpec] | None = None
    grouped_rows: list[list[str]] | None = None
    figures: list[FigureSpec] | None = None
    layout: list[int] | None = None

    if args.group_layout_file:
        grouped_groups, grouped_rows = parse_group_layout_file(
            folder,
            Path(args.group_layout_file).expanduser().resolve(),
        )
    else:
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
        if args.ai_width_mm is not None:
            artboard_width = mm_to_pt(args.ai_width_mm)

    if args.auto_height:
        if grouped_groups is not None and grouped_rows is not None:
            artboard_height = auto_height_for_groups(
                groups=grouped_groups,
                outer_rows=grouped_rows,
                artboard_width=artboard_width,
                margin=args.margin,
                gap=args.gap,
            )
        else:
            assert figures is not None and layout is not None
            artboard_height = auto_height_for_layout(
                figures=figures,
                layout=layout,
                artboard_width=artboard_width,
                margin=args.margin,
                gap=args.gap,
            )

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
    if grouped_groups is not None and grouped_rows is not None:
        print_group_summary(grouped_groups, grouped_rows, output_jsx, save_ai)
    else:
        assert figures is not None and layout is not None
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

    if grouped_groups is not None and grouped_rows is not None:
        jsx = generate_grouped_jsx(
            groups=grouped_groups,
            outer_rows=grouped_rows,
            artboard_width=artboard_width,
            artboard_height=artboard_height,
            margin=args.margin,
            gap=args.gap,
            add_labels=not args.no_labels,
            label_size=args.label_size,
            document_name=folder.name,
            save_ai=save_ai,
        )
    else:
        assert figures is not None and layout is not None
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
