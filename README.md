# PanelJet

`PanelJet` is a macOS-first tool for accelerating scientific figure assembly. It scans a folder of figure files, builds an Adobe Illustrator JSX layout script, and can optionally ask Illustrator to create an editable `.ai` document for you.

It is designed for workflows where you want:

- automatic panel packing from a folder of `pdf/png/jpg/tiff`
- `A/B/C/...` panel labels
- shape-aware ordering for standard, tall, and wide figures
- a real Illustrator document you can still tweak by hand
- a natural-language wrapper for Codex and Claude Code

## Platform support

Current workflow targets:

- macOS
- Adobe Illustrator installed locally
- `sips` available on the system

Currently macOS-focused; cross-platform support would require replacing `sips` and AppleScript integration.

## Requirements

- Python 3.10 or newer
- macOS
- Adobe Illustrator installed locally
- `sips` available on the system

## Install

### From a local checkout

```bash
pip install -e .
```

### From GitHub

```bash
pip install git+https://github.com/alexpengyl1/paneljet.git
```

## Quick start

Auto-detect figure sizes, use smart ordering and smart layout, generate JSX, and open Illustrator:

```bash
paneljet \
  /path/to/figure_folder \
  --order-mode smart \
  --layout-mode smart \
  --name Figure3_layout \
  --ai-width-mm 260 \
  --ai-height-mm 180 \
  --run-illustrator
```

This writes:

- `/path/to/figure_folder/Figure3_layout.jsx`
- `/path/to/figure_folder/Figure3_layout.ai`

## Demo

![PanelJet demo collage](./docs/assets/paneljet-demo-cats.png)

The demo collage above uses the author's two very fluffy ragdoll cats as sample data: Heli (male, 9.5 kg) and Huajuan (female, 4.5 kg).

## Common workflows

### 1. Auto-scan a folder and preview layout

```bash
paneljet \
  /path/to/figure_folder \
  --order-mode smart \
  --layout-mode smart \
  --dry-run
```

### 2. Use explicit order and labels

```bash
paneljet \
  /path/to/figure_folder \
  --files "A=plot1.pdf,B=plot2.pdf,C=plot3.pdf,D=plot4.pdf" \
  --layout 2,2 \
  --name Figure2_layout \
  --ai-width-mm 240 \
  --ai-height-mm 160 \
  --run-illustrator
```

### 3. Use a text file for order

Create `order.txt`:

```text
A=plot1.pdf
B=plot2.pdf
C=plot3.pdf
D=plot4.pdf
```

Then run:

```bash
paneljet \
  /path/to/figure_folder \
  --files-file /path/to/order.txt \
  --layout 2,2 \
  --name Figure2_layout
```

## Natural language with Codex and Claude Code

This repository includes both:

- a Codex skill at [`.codex/skills/paneljet`](./.codex/skills/paneljet)
- a Claude Code skill at [`.claude/skills/paneljet`](./.claude/skills/paneljet)

That means the same core tool can be used in three ways:

- direct CLI: `paneljet ...`
- Codex natural language wrapper
- Claude Code natural language wrapper

Example natural-language intents:

- "Pack this folder into an Illustrator figure and make it 260 by 180 mm."
- "Arrange these files as A-H and open Illustrator."
- "Preview the smart layout before generating the AI file."

## CLI reference

### Inputs

- `folder`: folder containing figure files
- `--files`: comma-separated explicit order, supports `A=file.pdf,B=file2.pdf`
- `--files-file`: text file with one filename or `label=filename` per line

### Ordering and layout

- `--order-mode natural|smart`
- `--layout-mode balanced|smart`
- `--layout 3,3,2`

### Output naming

- `--name Figure3_layout`
- `--output-jsx /path/to/file.jsx`
- `--save-ai /path/to/file.ai`

### Artboard sizing

- `--artboard-size A4|A3|letter|WxH`
- `--landscape`
- `--ai-width-mm 260 --ai-height-mm 180`

### Labels and spacing

- `--no-labels`
- `--label-size 18`
- `--margin 24`
- `--gap 16`

### Execution

- `--dry-run`
- `--run-illustrator`
- `--illustrator-app "Adobe Illustrator"`

## How it works

1. Scans the folder for supported input figures.
2. Uses `sips` to read width and height.
3. Classifies each figure as `standard`, `tall`, or `wide`.
4. Builds a row layout.
5. Writes an Illustrator JSX script that:
   - creates a new document
   - places each figure
   - scales proportionally
   - centers inside its cell
   - adds labels
   - optionally saves to `.ai`
6. Optionally tells Illustrator to run that JSX.

## Limitations

- currently macOS-focused
- depends on Illustrator scripting being enabled by the local system permissions
- current smart layout is heuristic, not a full optimization engine

## Development

Run locally without installing:

```bash
python -m paneljet.cli /path/to/figure_folder --dry-run
```

Or install editable:

```bash
pip install -e .
```

## About the author

PanelJet is created by Yueling Peng, a postdoctoral researcher at the University of Gothenburg with research experience spanning multi-omics, single-cell and spatial transcriptomics, machine learning, and translational immunology.

The tool was developed to speed up scientific figure assembly for manuscript and presentation workflows while keeping Adobe Illustrator output fully editable.

- GitHub: [alexpengyl1](https://github.com/alexpengyl1)
- Email: yueling.peng@gu.se

## License

MIT
