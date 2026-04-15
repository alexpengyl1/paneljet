---
name: paneljet
description: Pack scientific figures into an editable Adobe Illustrator layout from natural language. Use for requests about scanning a figure folder, smart ordering, manual panel order, panel labels, artboard size, and generating `.ai` output.
version: 0.2.0
author: xpenyu
license: MIT
tags: [illustrator, figure, panels, scientific-figures, ai, jsx, layout]
user_invocable: true
---

# PanelJet

Use `paneljet` when the user wants scientific figures packed into an Adobe Illustrator document.

## When to use

Trigger when the user wants to:

- combine a folder of PDFs or images into a panel figure
- add panel labels like `A`, `B`, `C`
- keep the result editable in Illustrator
- describe the layout in natural language instead of writing the CLI by hand

## Rules

- Prefer `--order-mode smart --layout-mode smart` when the user does not provide exact order.
- Use `--files` or `--files-file` when the user specifies exact file-to-label mapping.
- Use `--ai-width-mm` and `--ai-height-mm` when the user gives explicit output size.
- Use `--name` when the user wants specific output naming.
- Use `--dry-run` when the user wants to inspect the plan first.
- Use `--run-illustrator` when the user wants the `.ai` created immediately.

## Examples

Auto-layout and build the AI file:

```bash
paneljet "/path/to/folder" --order-mode smart --layout-mode smart --name Figure3 --ai-width-mm 260 --ai-height-mm 180 --run-illustrator
```

Manual file order:

```bash
paneljet "/path/to/folder" --files "A=plot1.pdf,B=plot2.pdf,C=plot3.pdf" --layout 2,1 --name Figure3 --run-illustrator
```

Preview only:

```bash
paneljet "/path/to/folder" --order-mode smart --layout-mode smart --dry-run
```
