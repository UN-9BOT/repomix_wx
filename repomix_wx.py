from __future__ import annotations

import os
import sys
import subprocess
import threading
from pathlib import Path
import json
import fnmatch

from datetime import datetime

import wx
import wx.lib.scrolledpanel as scrolled

IGNORED_DIRS_DEFAULT = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    ".gitignore",
    "uv.lock",
}


def discover_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        for name in filenames:
            p = dp / name
            # skip large binaries at start (minimal)
            if p.name.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".7z", ".tar", ".gz", ".bz2")
            ):
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            files.append(rel)
    files.sort()
    return files


class RepomixFrame(wx.Frame):
    def __init__(self) -> None:  # noqa: PLR0915
        super().__init__(None, title="Repomix GUI (minimal)", size=(wx.Size(1050, 720)))
        panel = scrolled.ScrolledPanel(self, style=wx.TAB_TRAVERSAL)

        # === Top panel: choose directory and output file ===
        self.dir_picker = wx.DirPickerCtrl(panel, message="Choose project/repository root")
        self.output_name = wx.TextCtrl(panel, value="repomix_output.md", style=wx.TE_PROCESS_ENTER)
        self.refresh_btn = wx.Button(panel, label="Reset")
        self.run_btn = wx.Button(panel, label="Run repomix")
        # Output style selection
        self.style_choice = wx.Choice(panel, choices=["markdown", "plain", "xml"])
        self.style_choice.SetSelection(0)

        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_sizer.Add(wx.StaticText(panel, label="Directory:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        top_sizer.Add(self.dir_picker, 1, wx.RIGHT, 8)
        top_sizer.Add(wx.StaticText(panel, label="Output (-o):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        top_sizer.Add(self.output_name, 0, wx.RIGHT, 8)
        top_sizer.Add(wx.StaticText(panel, label="Style (--style):"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        top_sizer.Add(self.style_choice, 0, wx.RIGHT, 8)
        top_sizer.Add(self.refresh_btn, 0, wx.RIGHT, 8)
        top_sizer.Add(self.run_btn, 0)

        # === Middle area: files and exclusions ===
        # Files (left)
        self.filter_input = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.filter_input.SetDescriptiveText("Filter by substring/extension…")
        self.files_list = wx.ListBox(panel, choices=[], style=wx.LB_EXTENDED)

        left_sizer = wx.BoxSizer(wx.VERTICAL)
        left_sizer.Add(wx.StaticText(panel, label="Included files"), 0, wx.BOTTOM, 4)
        left_sizer.Add(self.filter_input, 0, wx.EXPAND | wx.BOTTOM, 6)
        left_sizer.Add(self.files_list, 1, wx.EXPAND | wx.BOTTOM, 6)

        # Move selected items to exclusions
        to_exclude_btn = wx.Button(panel, label=">>")
        to_include_btn = wx.Button(panel, label="<<")

        mid_sizer = wx.BoxSizer(wx.VERTICAL)
        mid_sizer.AddStretchSpacer(1)
        mid_sizer.Add(to_exclude_btn, 0, wx.BOTTOM, 6)
        mid_sizer.Add(to_include_btn, 0)
        mid_sizer.AddStretchSpacer(1)

        # Exclusions (right): exact files and glob patterns
        self.excluded_files = wx.CheckListBox(panel, choices=[], style=wx.LB_EXTENDED)
        self.ignore_input = wx.TextCtrl(panel, value="", style=wx.TE_PROCESS_ENTER)
        add_ignore = wx.Button(panel, label=">>")
        remove_ignore = wx.Button(panel, label="<<")

        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer.Add(wx.StaticText(panel, label="Excluded files (exact paths)"), 0, wx.BOTTOM, 4)
        right_sizer.Add(self.excluded_files, 1, wx.EXPAND | wx.BOTTOM, 8)

        ig_box = wx.StaticBox(panel, label="Glob ignore patterns (--ignore)")
        ig_sizer = wx.StaticBoxSizer(ig_box, wx.VERTICAL)
        self.ignore_patterns = wx.ListBox(panel, choices=[], style=wx.LB_EXTENDED)
        ig_row = wx.BoxSizer(wx.HORIZONTAL)
        ig_row.Add(self.ignore_input, 1, wx.RIGHT, 6)
        ig_row.Add(add_ignore, 0)
        ig_sizer.Add(ig_row, 0, wx.EXPAND | wx.BOTTOM, 6)
        ig_sizer.Add(self.ignore_patterns, 1, wx.EXPAND | wx.BOTTOM, 6)
        ig_sizer.Add(remove_ignore, 0, wx.ALIGN_RIGHT)

        right_sizer.Add(ig_sizer, 1, wx.EXPAND, 0)

        # === Bottom: command preview + log ===
        self.cmd_preview = wx.TextCtrl(panel, style=wx.TE_READONLY | wx.TE_MULTILINE)
        self.cmd_preview.SetMinSize(wx.Size(-1, 60))
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)

        bottom_sizer = wx.BoxSizer(wx.VERTICAL)
        bottom_sizer.Add(wx.StaticText(panel, label="Command:"), 0, wx.BOTTOM, 4)
        bottom_sizer.Add(self.cmd_preview, 0, wx.EXPAND | wx.BOTTOM, 6)
        bottom_sizer.Add(wx.StaticText(panel, label="Log:"), 0, wx.BOTTOM, 4)
        bottom_sizer.Add(self.log, 1, wx.EXPAND)

        # === Overall layout ===
        center_sizer = wx.BoxSizer(wx.HORIZONTAL)
        center_sizer.Add(left_sizer, 1, wx.EXPAND | wx.RIGHT, 8)
        center_sizer.Add(mid_sizer, 0, wx.RIGHT, 8)
        center_sizer.Add(right_sizer, 1, wx.EXPAND)

        # === repomix options ===
        opts_box = wx.StaticBox(panel, label="repomix options")
        opts = wx.StaticBoxSizer(opts_box, wx.VERTICAL)

        grid = wx.GridSizer(rows=0, cols=3, vgap=4, hgap=12)
        self.cb_parsable = wx.CheckBox(panel, label="--parsable-style")
        self.cb_compress = wx.CheckBox(panel, label="--compress")
        self.cb_line_numbers = wx.CheckBox(panel, label="--output-show-line-numbers")
        self.cb_no_file_summary = wx.CheckBox(panel, label="--no-file-summary")
        self.cb_no_dir_structure = wx.CheckBox(panel, label="--no-directory-structure")
        self.cb_no_files = wx.CheckBox(panel, label="--no-files")
        self.cb_remove_comments = wx.CheckBox(panel, label="--remove-comments")
        self.cb_remove_empty = wx.CheckBox(panel, label="--remove-empty-lines")
        # Enabled by default
        self.cb_remove_empty.SetValue(True)
        self.cb_truncate_b64 = wx.CheckBox(panel, label="--truncate-base64")
        self.cb_include_empty_dirs = wx.CheckBox(panel, label="--include-empty-directories")
        self.cb_no_git_sort = wx.CheckBox(panel, label="--no-git-sort-by-changes")
        self.cb_include_diffs = wx.CheckBox(panel, label="--include-diffs")
        self.cb_include_logs = wx.CheckBox(panel, label="--include-logs")

        # smaller font for options
        base_font = panel.GetFont()
        size = max(7, base_font.GetPointSize() - 1)
        small_font = wx.Font(size, base_font.GetFamily(), base_font.GetStyle(), wx.FONTWEIGHT_NORMAL)
        opts_box.SetFont(small_font)

        for cb in [
            self.cb_parsable, self.cb_compress,
            self.cb_line_numbers, self.cb_no_file_summary,
            self.cb_no_dir_structure, self.cb_no_files,
            self.cb_remove_comments, self.cb_remove_empty,
            self.cb_truncate_b64, self.cb_include_empty_dirs,
            self.cb_no_git_sort, self.cb_include_diffs,
            self.cb_include_logs,
        ]:
            cb.SetFont(small_font)
            grid.Add(cb, 0, wx.ALIGN_LEFT)

        opts.Add(grid, 0, wx.EXPAND | wx.BOTTOM, 6)

        # Header and instructions in one row (2 columns)
        params_row = wx.BoxSizer(wx.HORIZONTAL)

        col1 = wx.BoxSizer(wx.VERTICAL)
        lbl_header = wx.StaticText(panel, label="Header (--header-text):")
        lbl_header.SetFont(small_font)
        self.header_text = wx.TextCtrl(panel, value="", style=wx.TE_PROCESS_ENTER)
        self.header_text.SetFont(small_font)
        col1.Add(lbl_header, 0, wx.BOTTOM, 2)
        col1.Add(self.header_text, 0, wx.EXPAND)

        col2 = wx.BoxSizer(wx.VERTICAL)
        lbl_instr = wx.StaticText(panel, label="Instructions (--instruction-file-path):")
        lbl_instr.SetFont(small_font)
        self.instructions_picker = wx.FilePickerCtrl(panel, message="Choose instructions file")
        try:
            self.instructions_picker.SetFont(small_font)
        except Exception as e:
            self._append_log(f"SetFont error: {e}")
        col2.Add(lbl_instr, 0, wx.BOTTOM, 2)
        col2.Add(self.instructions_picker, 0, wx.EXPAND)

        params_row.Add(col1, 1, wx.RIGHT | wx.EXPAND, 12)
        params_row.Add(col2, 1, wx.EXPAND, 0)
        opts.Add(params_row, 0, wx.EXPAND)

        root_sizer = wx.BoxSizer(wx.VERTICAL)
        root_sizer.Add(top_sizer, 0, wx.EXPAND | wx.ALL, 8)
        root_sizer.Add(center_sizer, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        root_sizer.Add(opts, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        root_sizer.Add(bottom_sizer, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.SetSizer(root_sizer)
        panel.SetupScrolling(scroll_x=False, scroll_y=True)

        # State
        self.root_path: Path | None = None
        self._all_files: list[Path] = []  # all found
        self._visible_files: list[Path] = []  # after filtering
        self._excluded_files_set: set[Path] = set()
        self._ignore_patterns: list[str] = []
        self._ignore_defaults_optout: set[str] = set()

        # Events
        self.dir_picker.Bind(wx.EVT_DIRPICKER_CHANGED, self.on_dir_changed)
        self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh)
        self.filter_input.Bind(wx.EVT_TEXT_ENTER, self.on_filter)
        self.filter_input.Bind(wx.EVT_TEXT, self.on_filter)

        # Bulk selection buttons removed — the left list reflects current state

        to_exclude_btn.Bind(wx.EVT_BUTTON, self.on_to_exclude)
        to_include_btn.Bind(wx.EVT_BUTTON, self.on_to_include)

        add_ignore.Bind(wx.EVT_BUTTON, self.on_add_ignore)
        self.ignore_input.Bind(wx.EVT_TEXT_ENTER, self.on_add_ignore)
        remove_ignore.Bind(wx.EVT_BUTTON, self.on_remove_ignore)

        self.run_btn.Bind(wx.EVT_BUTTON, self.on_run)
        self.style_choice.Bind(wx.EVT_CHOICE, self.on_style_change)
        self.output_name.Bind(wx.EVT_TEXT, self._on_command_changed_evt)
        self.output_name.Bind(wx.EVT_TEXT_ENTER, self._on_command_changed_evt)
        # Binds for additional options
        for cb in [
            self.cb_parsable, self.cb_compress,
            self.cb_line_numbers, self.cb_no_file_summary,
            self.cb_no_dir_structure, self.cb_no_files,
            self.cb_remove_comments, self.cb_remove_empty,
            self.cb_truncate_b64, self.cb_include_empty_dirs,
            self.cb_no_git_sort, self.cb_include_diffs,
            self.cb_include_logs,
        ]:
            cb.Bind(wx.EVT_CHECKBOX, self._on_command_changed_evt)
        self.header_text.Bind(wx.EVT_TEXT, self._on_command_changed_evt)
        self.header_text.Bind(wx.EVT_TEXT_ENTER, self._on_command_changed_evt)
        self.instructions_picker.Bind(wx.EVT_FILEPICKER_CHANGED, self._on_command_changed_evt)
        self._update_cmd_preview()

        # Init root directory and options: CLI argument or cache
        self._init_root_from_args_or_cache()

    # ==== UI logic ====
    def on_dir_changed(self, _evt: wx.Event) -> None:
        self.root_path = Path(self.dir_picker.GetPath()).resolve()
        self._append_log(f"Selected directory: {self.root_path}")
        self.on_refresh(None)
        self._persist_state()

    def on_refresh(self, _evt: wx.Event | None) -> None:
        if not self.root_path:
            return
        self._append_log("Scanning directory…")
        self._all_files = discover_files(self.root_path)
        self._excluded_files_set.clear()
        # Add default ignores if directories actually exist in the root
        self._ensure_default_ignores_exist()
        self._visible_files = list(self._all_files)
        self._reload_files_list()
        # Sync the right-hand patterns list with the current state
        self.ignore_patterns.Set(self._ignore_patterns)
        self._update_cmd_preview()
        self.log.SetValue(f"Ready: found files: {len(self._all_files)}\n")
        self._append_log(
            f"Current ignore patterns: {', '.join(self._ignore_patterns) if self._ignore_patterns else '—'}"
        )

    def _reload_files_list(self) -> None:
        q = (self.filter_input.GetValue() or "").strip().lower()
        if q:
            self._append_log(f"Filter: '{q}'")

        base = [p for p in self._all_files if not self._is_ignored_path(p) and p not in self._excluded_files_set]
        if q:
            self._visible_files = [p for p in base if q in str(p).lower()]
        else:
            self._visible_files = list(base)

        # show only included files
        choices = [str(p) for p in self._visible_files]
        self.files_list.Set(choices)

        # excluded (exact paths), but hide ones covered by glob patterns
        self.excluded_files.Set([str(p) for p in sorted(self._excluded_files_set) if not self._is_ignored_path(p)])
        self._append_log(
            f"Included files: {len(self._visible_files)} | Exact exclusions: {len(self._excluded_files_set)}"
        )

    def on_filter(self, _evt: wx.Event) -> None:
        self._rescan_and_update()

    def on_style_change(self, _evt: wx.Event) -> None:
        try:
            style = self.style_choice.GetStringSelection()
        except Exception:
            style = "markdown"
        ext_map = {"markdown": ".md", "plain": ".txt", "xml": ".xml"}
        want_ext = ext_map.get(style, ".md")
        current = (self.output_name.GetValue() or "repomix_output").strip()
        p = Path(current)
        stem = p.stem or "repomix_output"
        new_name = f"{stem}{want_ext}"
        if new_name != current:
            self.output_name.SetValue(new_name)
            self._append_log(f"Style: {style} → output: {new_name}")
        self._on_command_changed()

    def on_to_exclude(self, _evt: wx.Event) -> None:
        sel = self.files_list.GetSelections()
        for i in sel:
            p = self._visible_files[i]
            self._excluded_files_set.add(p)
        if sel:
            self._append_log(f"Added to exclusions: {len(sel)}")
        self._rescan_and_update()
        self._persist_state()

    def on_to_include(self, _evt: wx.Event) -> None:
        sel = self.excluded_files.GetSelections()
        items = [self.excluded_files.GetString(i) for i in sel]
        for s in items:
            self._excluded_files_set.discard(Path(s))
        if items:
            self._append_log(f"Removed from exclusions: {len(items)}")
        self._rescan_and_update()
        self._persist_state()

    def on_add_ignore(self, _evt: wx.Event) -> None:
        pat = self.ignore_input.GetValue().strip()
        if pat:
            self._ignore_patterns.append(pat)
            self.ignore_patterns.Append(pat)
            self.ignore_input.SetValue("")
            # if pattern matches a default, clear opt-out
            if pat in IGNORED_DIRS_DEFAULT and pat in self._ignore_defaults_optout:
                self._ignore_defaults_optout.discard(pat)
            self._append_log(f"Added glob pattern: {pat}")
            self._rescan_and_update()
            self._persist_state()

    def on_remove_ignore(self, _evt: wx.Event) -> None:
        sel = list(self.ignore_patterns.GetSelections())
        # Remember which patterns are being removed
        removed_patterns = [self._ignore_patterns[i] for i in sel]
        sel.reverse()
        for i in sel:
            self._ignore_patterns.pop(i)
            self.ignore_patterns.Delete(i)
        # After removal: files previously hidden by these patterns
        # should appear on the left; if some were in exact exclusions — unexclude them.
        def matches_any(p: Path, patterns: list[str]) -> bool:
            sp = str(p).replace("\\", "/")
            return self._matches_patterns(sp, patterns)

        remaining = list(self._ignore_patterns)
        to_unexclude = [p for p in list(self._excluded_files_set)
                        if matches_any(p, removed_patterns) and not matches_any(p, remaining)]
        for p in to_unexclude:
            self._excluded_files_set.discard(p)
        # If default patterns were removed — remember opt-out to avoid re-adding them
        for rp in removed_patterns:
            if rp in IGNORED_DIRS_DEFAULT:
                self._ignore_defaults_optout.add(rp)
        if removed_patterns:
            self._append_log(f"Removed glob patterns: {', '.join(removed_patterns)}")
        self._rescan_and_update()
        self._persist_state()

    # ==== Build command and run ====
    def _build_command(self) -> list[str]:
        cmd = ["repomix"]
        out_name = (self.output_name.GetValue() or "repomix_output.md").strip()
        if out_name:
            cmd += ["-o", out_name]
        # output style
        try:
            style = self.style_choice.GetStringSelection() or "markdown"
        except Exception:
            style = "markdown"
        if style:
            cmd += ["--style", style]
        # boolean flags
        def add_flag(cb: wx.CheckBox, flag: str) -> None:
            try:
                if cb.GetValue():
                    cmd.append(flag)
            except Exception as e:
                self._append_log(f"flag read error: {e}")

        add_flag(self.cb_parsable, "--parsable-style")
        add_flag(self.cb_compress, "--compress")
        add_flag(self.cb_line_numbers, "--output-show-line-numbers")
        add_flag(self.cb_no_file_summary, "--no-file-summary")
        add_flag(self.cb_no_dir_structure, "--no-directory-structure")
        add_flag(self.cb_no_files, "--no-files")
        add_flag(self.cb_remove_comments, "--remove-comments")
        add_flag(self.cb_remove_empty, "--remove-empty-lines")
        add_flag(self.cb_truncate_b64, "--truncate-base64")
        add_flag(self.cb_include_empty_dirs, "--include-empty-directories")
        add_flag(self.cb_no_git_sort, "--no-git-sort-by-changes")
        add_flag(self.cb_include_diffs, "--include-diffs")
        add_flag(self.cb_include_logs, "--include-logs")

        # options with values
        ht = (self.header_text.GetValue() or "").strip()
        if ht:
            cmd += ["--header-text", ht]
        instr = (self.instructions_picker.GetPath() or "").strip()
        if instr:
            cmd += ["--instruction-file-path", instr]
        # Combine glob patterns and exact excluded paths for --ignore
        ignore_items: list[str] = []
        if self._ignore_patterns:
            ignore_items.extend(self._ignore_patterns)
        if self._excluded_files_set:
            ignore_items.extend(str(p) for p in sorted(self._excluded_files_set))
        if ignore_items:
            cmd += ["--ignore", ",".join(ignore_items)]
        return cmd

    def _update_cmd_preview(self) -> None:
        cmd = self._build_command()
        preview = " ".join([self._shell_quote(x) for x in cmd])
        self.cmd_preview.SetValue(preview)
        self._append_log("Command updated")

    @staticmethod
    def _shell_quote(s: str) -> str:
        if not s or any(ch in s for ch in " \t\"'*$&()[]{};|<>`"):
            return '"' + s.replace('"', r"\"") + '"'
        return s

    def on_run(self, _evt: wx.Event) -> None:
        if not self.root_path:
            wx.MessageBox("Select a project directory first", "Error", wx.ICON_ERROR | wx.OK, self)
            return

        cmd = self._build_command()
        self.log.SetValue(f">>> CWD: {self.root_path}\n>>> CMD: {' '.join(cmd)}\n\n")
        self._append_log("Starting repomix…")

        # Run in a separate thread to avoid blocking the UI
        def run() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.root_path),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                out, _ = proc.communicate()
                wx.CallAfter(self.log.AppendText, out)
                wx.CallAfter(self.log.AppendText, f"\n>>> Exit code: {proc.returncode}\n")
                if proc.returncode == 0:
                    wx.CallAfter(wx.MessageBox, "Done! Output written.", "repomix", wx.ICON_INFORMATION | wx.OK, self)
                else:
                    wx.CallAfter(
                        wx.MessageBox,
                        "repomix exited with non-zero status. See log.",
                        "Error",
                        wx.ICON_ERROR | wx.OK,
                        self,
                    )
            except FileNotFoundError:
                wx.CallAfter(
                    wx.MessageBox,
                    "Executable 'repomix' not found. Install: pip install repomix",
                    "Error",
                    wx.ICON_ERROR | wx.OK,
                    self,
                )
            except Exception as e:
                wx.CallAfter(wx.MessageBox, f"Launch error: {e}", "Error", wx.ICON_ERROR | wx.OK, self)

        threading.Thread(target=run, daemon=True).start()

    # ==== State updates and rescan ====
    def _on_command_changed(self) -> None:
        # Any command parameter change: rescan and update the UI
        self._append_log("Command parameters changed")
        self._rescan_and_update()
        self._persist_state()

    def _on_command_changed_evt(self, _evt: wx.Event) -> None:
        self._on_command_changed()

    def _rescan_and_update(self) -> None:
        if not self.root_path:
            return
        self._append_log("Rescanning directory…")
        self._all_files = discover_files(self.root_path)
        # ensure default ignores are reflected in patterns
        self._ensure_default_ignores_exist()
        self._visible_files = list(self._all_files)
        self._reload_files_list()
        # sync the right-hand patterns list
        self.ignore_patterns.Set(self._ignore_patterns)
        self._update_cmd_preview()

    # ==== Pattern matching helpers ====
    def _normalize_glob(self, pat: str) -> str:
        s = (pat or "").strip().replace("\\", "/")
        s = s.removeprefix("./")
        if len(s) > 1 and s.endswith("/"):
            s = s[:-1]
        return s

    def _matches_patterns(self, path_str: str, patterns: list[str]) -> bool:
        ps = path_str.replace("\\", "/")
        for raw in patterns:
            pat = self._normalize_glob(raw)
            if not pat:
                continue
            if ps == pat:
                return True
            if fnmatch.fnmatch(ps, pat):
                return True
            # if a top-level directory is specified, match by prefix
            if "/" not in pat and ps.startswith(pat + "/"):
                return True
        return False

    def _is_ignored_path(self, p: Path) -> bool:
        return self._matches_patterns(str(p), self._ignore_patterns)

    # ==== State cache (directory and options) ====
    def _ensure_default_ignores_exist(self) -> None:
        if not self.root_path:
            return
        existing = set(self._ignore_patterns)
        added: list[str] = []
        for d in sorted(IGNORED_DIRS_DEFAULT):
            path = (self.root_path / d)
            if path.exists():
                if d not in existing and d not in self._ignore_defaults_optout:
                    self._ignore_patterns.append(d)
                    added.append(d)
                    existing.add(d)
        if added:
            self._append_log(f"Auto-added ignores: {', '.join(added)}")

    def _init_root_from_args_or_cache(self) -> None:
        try:
            arg = sys.argv[1] if len(sys.argv) > 1 else None
            if arg:
                p = Path(arg).expanduser()
                if p.is_dir():
                    self.dir_picker.SetPath(str(p))
                    self.root_path = p.resolve()
                    self._append_log(f"Started from argument: {self.root_path}")
                    self.on_refresh(None)
                    self._persist_state()
                    return
        except Exception as e:
            self._append_log(f"Init arg error: {e}")
        # if the argument is missing or invalid — try cache (including options)
        self._restore_state()

    def _cache_dir(self) -> Path:
        base = os.getenv("XDG_CACHE_HOME")
        if base:
            return Path(base) / "RepomixGUI"
        return Path.home() / ".cache" / "RepomixGUI"

    def _state_path(self) -> Path:
        return self._cache_dir() / "state.json"

    def _persist_state(self) -> None:
        try:
            cache_dir = self._cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            # collect state
            flags = {
                "parsable": bool(self.cb_parsable.GetValue()),
                "compress": bool(self.cb_compress.GetValue()),
                "line_numbers": bool(self.cb_line_numbers.GetValue()),
                "no_file_summary": bool(self.cb_no_file_summary.GetValue()),
                "no_directory_structure": bool(self.cb_no_dir_structure.GetValue()),
                "no_files": bool(self.cb_no_files.GetValue()),
                "remove_comments": bool(self.cb_remove_comments.GetValue()),
                "remove_empty": bool(self.cb_remove_empty.GetValue()),
                "truncate_b64": bool(self.cb_truncate_b64.GetValue()),
                "include_empty_dirs": bool(self.cb_include_empty_dirs.GetValue()),
                "no_git_sort": bool(self.cb_no_git_sort.GetValue()),
                "include_diffs": bool(self.cb_include_diffs.GetValue()),
                "include_logs": bool(self.cb_include_logs.GetValue()),
            }
            state = {
                "last_dir": self.dir_picker.GetPath(),
                "output_name": self.output_name.GetValue(),
                "style": self.style_choice.GetStringSelection(),
                "header_text": self.header_text.GetValue(),
                "instruction_file_path": self.instructions_picker.GetPath(),
                "flags": flags,
                "ignore_patterns": list(self._ignore_patterns),
                "ignore_defaults_optout": list(self._ignore_defaults_optout),
                "excluded_files": [str(p) for p in sorted(self._excluded_files_set)],
            }
            self._state_path().write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            self._append_log(f"Persist error: {e}")

    def _restore_state(self) -> None:
        try:
            p = self._state_path()
            if not p.is_file():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            # directory
            last_dir = data.get("last_dir")
            if last_dir and Path(last_dir).is_dir():
                self.dir_picker.SetPath(last_dir)
                self.root_path = Path(last_dir).resolve()
            # options
            style = data.get("style") or "markdown"
            try:
                idx = self.style_choice.FindString(style)
                if idx != wx.NOT_FOUND:
                    self.style_choice.SetSelection(idx)
            except Exception as e:
                self._append_log(f"Style restore error: {e}")
            self.output_name.SetValue(data.get("output_name") or self.output_name.GetValue())
            self.header_text.SetValue(data.get("header_text") or "")
            instr = data.get("instruction_file_path") or ""
            if instr:
                try:
                    self.instructions_picker.SetPath(instr)
                except Exception as e:
                    self._append_log(f"Instr path error: {e}")
            flags = data.get("flags") or {}
            # set checkboxes
            self.cb_parsable.SetValue(bool(flags.get("parsable")))
            self.cb_compress.SetValue(bool(flags.get("compress")))
            self.cb_line_numbers.SetValue(bool(flags.get("line_numbers")))
            self.cb_no_file_summary.SetValue(bool(flags.get("no_file_summary")))
            self.cb_no_dir_structure.SetValue(bool(flags.get("no_directory_structure")))
            self.cb_no_files.SetValue(bool(flags.get("no_files")))
            self.cb_remove_comments.SetValue(bool(flags.get("remove_comments")))
            # remove_empty is True by default, but can be overridden by saved value
            self.cb_remove_empty.SetValue(bool(flags.get("remove_empty", True)))
            self.cb_truncate_b64.SetValue(bool(flags.get("truncate_b64")))
            self.cb_include_empty_dirs.SetValue(bool(flags.get("include_empty_dirs")))
            self.cb_no_git_sort.SetValue(bool(flags.get("no_git_sort")))
            self.cb_include_diffs.SetValue(bool(flags.get("include_diffs")))
            self.cb_include_logs.SetValue(bool(flags.get("include_logs")))

            # ignore/exclude lists
            self._ignore_patterns = list(data.get("ignore_patterns") or [])
            self._ignore_defaults_optout = set(data.get("ignore_defaults_optout") or [])
            self._excluded_files_set = {Path(s) for s in data.get("excluded_files") or []}

            if self.root_path:
                self._append_log(f"Start from cache: {self.root_path}")
                self.on_refresh(None)
        except Exception as e:
            self._append_log(f"Restore error: {e}")

    # ==== Logger ====
    def _append_log(self, msg: str) -> None:
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            self.log.AppendText(f"[{ts}] {msg}\n")
        except Exception as _e:
            sys.stderr.write(f"[repomix_wx] log error: {_e}\n")


class RepomixApp(wx.App):
    def OnInit(self) -> bool:  # noqa: N802 (wx API)
        self.SetAppName("RepomixGUI")
        frame = RepomixFrame()
        frame.Show()
        return True


if __name__ == "__main__":
    app = RepomixApp(False)
    app.MainLoop()
