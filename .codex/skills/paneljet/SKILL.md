---
name: "paneljet"
description: "Use when a user wants to combine a folder of scientific figures into an editable Adobe Illustrator layout using natural language. Translate requests about folder path, ordering, labels, artboard size, layout, and output naming into `paneljet` CLI commands."
---

# PanelJet

Use `paneljet` to turn a folder of scientific figures into an editable Illustrator layout with panel labels.

## Trigger conditions

Use this skill when the user says things like:

- "Pack these figures into Illustrator"
- "Make me a figure layout from this folder"
- "Arrange these PDFs as A-H"
- "Generate an AI file from these plots"
- "Use a 260 mm by 180 mm artboard"

## Defaults

- If the user gives only a folder, prefer:
  - `--order-mode smart`
  - `--layout-mode smart`
- If the user specifies exact order, use `--files` or `--files-file`.
- If the user asks for a final Illustrator document, include `--run-illustrator`.
- If the user asks only to inspect the plan first, use `--dry-run`.
- If the user asks for a named output, use `--name`.
- If the user gives exact width and height, map them to `--ai-width-mm` and `--ai-height-mm`.

## Workflow

1. Identify the figure folder.
2. Determine whether the user wants:
   - automatic shape-aware ordering
   - manual order and labels
   - a dry run
   - immediate Illustrator execution
3. Build the `paneljet` command.
4. Run it.
5. Report the generated `.jsx` and `.ai` paths.

## Command templates

Auto layout:

```bash
paneljet "/path/to/folder" --order-mode smart --layout-mode smart --name Figure3 --ai-width-mm 260 --ai-height-mm 180 --run-illustrator
```

Manual order:

```bash
paneljet "/path/to/folder" --files "A=plot1.pdf,B=plot2.pdf,C=plot3.pdf" --layout 2,1 --name Figure3 --run-illustrator
```

Preview:

```bash
paneljet "/path/to/folder" --order-mode smart --layout-mode smart --dry-run
```
