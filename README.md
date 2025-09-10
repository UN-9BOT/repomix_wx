# Repomix GUI (wxPython)

Minimal GUI wrapper around the `repomix` CLI to generate compact repository overviews for LLM prompts.

## Features

- Choose project/repo directory (optionally via CLI arg)
- Included files list (auto-excludes exact and glob patterns)
- Exclusions:
  - exact file paths (right panel)
  - glob ignore patterns (`--ignore`)
- Command preview with auto-wrap
- Run `repomix` in selected root
- Output style selector (`--style`: markdown/plain/xml) with auto filename extension
- Common flags toggles (compress, parsable, no-* sections, diffs/logs, etc.)
- Persist options/state in `~/.cache/RepomixGUI/state.json`
- Scrollable UI, compact options (3 columns)

## Requirements

- Python 3.10+
- `repomix` (CLI)
- `wxPython`

Install from `requirements.txt` (Linux/macOS):

```
make venv install
```

## Run

```
.venv/bin/python repomix_wx.py /path/to/project
```

Output file defaults to `repomix_output.md`. Change style to adjust extension.

## Notes on ignores

- Default ignores (e.g., `.git`, `node_modules`, `.venv`, etc.) are auto-added to glob patterns if present in the root. Removing a default pattern records an opt-out, so it won’t reappear automatically.
- Exact exclusions list hides entries covered by current glob patterns.

## Packaging/Tips

- wxPython wheels may require system packages on some distros. Refer to https://wxpython.org/pages/downloads/ if installation fails.
- `repomix` CLI must be on PATH in the virtualenv.
