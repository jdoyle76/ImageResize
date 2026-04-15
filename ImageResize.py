#!/usr/bin/env python3
"""ImageResize — Batch image resizing and quality adjustment TUI."""

# ──────────────────────────────────────────────
# Standard library
# ──────────────────────────────────────────────
from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ──────────────────────────────────────────────
# Third-party
# ──────────────────────────────────────────────
from PIL import Image, UnidentifiedImageError
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Collapsible,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    RadioButton,
    RadioSet,
    Static,
)
from textual.worker import Worker, get_current_worker

APP_CSS = """
Screen {
    background: $surface;
}

#setup-screen {
    padding: 1 2;
}

.section-title {
    text-style: bold;
    color: $accent;
    margin-top: 1;
}

.dir-field {
    margin-bottom: 1;
}

#resolution-inputs {
    margin-top: 1;
    padding: 0 2;
}

#quality-row {
    height: 3;
    align: left middle;
}

#quality-label {
    width: 30;
}

#preset-row {
    margin-top: 1;
    height: 3;
    align: left middle;
}

#processing-screen {
    align: center middle;
    padding: 2 4;
}

#progress-bar {
    width: 80%;
}

#current-file {
    margin-top: 1;
    color: $text-muted;
}

#file-counter {
    margin-top: 1;
}

#summary-screen {
    align: center middle;
    padding: 2 4;
}

.stat-row {
    height: 2;
}

.stat-label {
    width: 30;
}

.stat-value {
    text-style: bold;
}

#error-log {
    margin-top: 1;
    max-height: 10;
}

#summary-buttons {
    margin-top: 2;
    height: 3;
    align: center middle;
}

DirectoryTree {
    width: 60;
    height: 20;
}

.modal-container {
    background: $surface;
    border: thick $accent;
    padding: 1 2;
    width: 70;
    height: auto;
}

#inputs-fixed, #inputs-max, #inputs-pct, #inputs-preset {
    display: none;
    margin-top: 1;
}

#inputs-max {
    display: block;
}

.num-input {
    width: 10;
}
"""

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────

_SETTINGS_DIR = Path.home() / ".imageresize"
_SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

_DEFAULT_SETTINGS: dict = {
    "last_used": {
        "source_dir": "",
        "target_dir": "",
        "resolution_mode": "max",
        "resolution_params": {"size": 1280, "by": "either"},
        "quality": 85,
    },
    "presets": {},
}


class SettingsManager:
    def __init__(self, settings_path: Path = _SETTINGS_FILE) -> None:
        self.settings_path = settings_path
        self._data: dict = {}

    def load(self) -> dict:
        self._data = copy.deepcopy(_DEFAULT_SETTINGS)
        try:
            if self.settings_path.exists():
                with open(self.settings_path) as f:
                    loaded = json.load(f)
                # Merge top-level keys from file over defaults
                for key, value in loaded.items():
                    self._data[key] = value
        except (json.JSONDecodeError, OSError):
            pass  # _data already set to defaults above
        return self._data

    def save(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        tmp.replace(self.settings_path)

    def get_last_used(self) -> dict:
        return copy.deepcopy(self._data.get("last_used", _DEFAULT_SETTINGS["last_used"]))

    def set_last_used(self, settings: dict) -> None:
        self._data["last_used"] = settings
        self.save()

    def get_presets(self) -> dict:
        return copy.deepcopy(self._data.get("presets", {}))

    def save_preset(self, name: str, settings: dict) -> None:
        self._data.setdefault("presets", {})[name] = settings
        self.save()

    def delete_preset(self, name: str) -> None:
        self._data.get("presets", {}).pop(name, None)
        self.save()

# ──────────────────────────────────────────────
# Processing engine
# ──────────────────────────────────────────────

@dataclass
class ResolutionParams:
    mode: str  # "fixed" | "max" | "percentage"
    # fixed mode
    width: Optional[int] = None
    height: Optional[int] = None
    fit: str = "letterbox"  # "stretch" | "letterbox" | "crop"
    # max mode
    size: Optional[int] = None
    by: str = "either"  # "width" | "height" | "either"
    # percentage mode
    percent: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "ResolutionParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None or k == "mode"}


@dataclass
class ProcessResult:
    processed: int = 0
    skipped: int = 0
    renamed: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)
    cancelled: bool = False


class ImageProcessor:
    def __init__(self) -> None:
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def map_quality(self, quality_pct: int, fmt: str) -> dict:
        """Map 0–100% quality to format-native save kwargs."""
        upper = fmt.upper()
        if upper in ("JPEG", "JPG"):
            return {"quality": max(1, min(95, round(quality_pct * 95 / 100)))}
        if upper == "PNG":
            return {"compress_level": round((100 - quality_pct) * 9 / 100)}
        if upper == "WEBP":
            return {"quality": max(1, min(100, quality_pct))}
        return {}

    def apply_resolution(self, img: Image.Image, params: ResolutionParams) -> Image.Image:
        if params.mode == "fixed":
            return self._apply_fixed(img, params)
        if params.mode == "max":
            return self._apply_max(img, params)
        if params.mode == "percentage":
            return self._apply_percentage(img, params)
        return img

    def _apply_fixed(self, img: Image.Image, params: ResolutionParams) -> Image.Image:
        target = (params.width, params.height)
        if params.fit == "stretch":
            return img.resize(target, Image.LANCZOS)
        if params.fit == "letterbox":
            img = img.copy()
            img.thumbnail(target, Image.LANCZOS)
            fill = (0,) * len(img.getbands())
            result = Image.new(img.mode, target, fill)
            offset = ((target[0] - img.width) // 2, (target[1] - img.height) // 2)
            result.paste(img, offset)
            return result
        if params.fit == "crop":
            ratio = max(target[0] / img.width, target[1] / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
            left = (img.width - target[0]) // 2
            top = (img.height - target[1]) // 2
            return img.crop((left, top, left + target[0], top + target[1]))
        return img

    def _apply_max(self, img: Image.Image, params: ResolutionParams) -> Image.Image:
        w, h = img.size
        s = params.size
        by = params.by
        if by == "width":
            if w <= s:
                return img
            ratio = s / w
        elif by == "height":
            if h <= s:
                return img
            ratio = s / h
        else:  # "either"
            if w <= s and h <= s:
                return img
            ratio = min(s / w, s / h)
        new_size = (max(1, int(w * ratio)), max(1, int(h * ratio)))
        return img.resize(new_size, Image.LANCZOS)

    def _apply_percentage(self, img: Image.Image, params: ResolutionParams) -> Image.Image:
        ratio = params.percent / 100.0
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        return img.resize(new_size, Image.LANCZOS)

    def _get_output_path(
        self, source_file: Path, source_root: Path, target_root: Path
    ) -> Path:
        rel = source_file.relative_to(source_root)
        return target_root / rel

    def _resolve_collision(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix, parent = path.stem, path.suffix, path.parent
        i = 1
        while True:
            candidate = parent / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    def process_batch(
        self,
        source_dir: Path,
        target_dir: Path,
        resolution_params: ResolutionParams,
        quality: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> ProcessResult:
        self._cancel_event.clear()
        result = ProcessResult()

        all_files = [f for f in source_dir.rglob("*") if f.is_file()]
        total = len(all_files)

        for i, source_file in enumerate(all_files):
            if self._cancel_event.is_set():
                result.cancelled = True
                break

            rel_name = str(source_file.relative_to(source_dir))
            if progress_callback:
                progress_callback(i, total, rel_name)

            try:
                with Image.open(source_file) as img:
                    fmt = img.format or source_file.suffix.lstrip(".").upper()
                    img.load()
                    out_img = self.apply_resolution(img, resolution_params)

                    out_path = self._get_output_path(source_file, source_dir, target_dir)
                    out_path.parent.mkdir(parents=True, exist_ok=True)

                    if out_path.exists():
                        out_path = self._resolve_collision(out_path)
                        result.renamed += 1

                    quality_kwargs = self.map_quality(quality, fmt)
                    out_img.save(out_path, format=fmt, **quality_kwargs)
                    result.processed += 1

            except UnidentifiedImageError:
                result.skipped += 1
            except Exception as exc:
                result.failed += 1
                result.errors.append((rel_name, str(exc)))

        if progress_callback and not result.cancelled:
            progress_callback(total, total, "")

        return result

# ──────────────────────────────────────────────
# TUI — Modals
# ──────────────────────────────────────────────

class DirectoryModal(ModalScreen):
    """Browse the filesystem and return the chosen directory path."""

    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]

    def __init__(self, start_path: str = "") -> None:
        super().__init__()
        self._start = Path(start_path).expanduser() if start_path else Path.home()
        self._selected: Optional[Path] = None

    def compose(self) -> ComposeResult:
        with Container(classes="modal-container"):
            yield Label("Select a directory (Enter to confirm, Esc to cancel)")
            yield DirectoryTree(str(self._start), id="dir-tree")
            yield Horizontal(
                Button("Select", variant="primary", id="btn-select", disabled=True),
                Button("Cancel", id="btn-cancel"),
            )

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self._selected = event.path
        self.query_one("#btn-select", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-select":
            self.dismiss(self._selected)
        else:
            self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class PresetNameModal(ModalScreen):
    """Prompt the user to enter a preset name."""

    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(classes="modal-container"):
            yield Label("Enter a name for this preset:")
            yield Input(placeholder="e.g. web, thumbnail, print", id="preset-name-input")
            yield Horizontal(
                Button("Save", variant="primary", id="btn-save"),
                Button("Cancel", id="btn-cancel"),
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            name = self.query_one("#preset-name-input", Input).value.strip()
            self.dismiss(name if name else None)
        else:
            self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class PresetSelectModal(ModalScreen):
    """Show a list of saved presets and return the chosen name."""

    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]

    def __init__(self, preset_names: list[str]) -> None:
        super().__init__()
        self._names = preset_names
        self._id_to_name: dict[str, str] = {f"preset-{i}": name for i, name in enumerate(preset_names)}

    def compose(self) -> ComposeResult:
        with Container(classes="modal-container"):
            yield Label("Select a preset:")
            yield ListView(
                *[ListItem(Label(name), id=f"preset-{i}") for i, name in enumerate(self._names)],
                id="preset-list",
            )
            yield Button("Cancel", id="btn-cancel")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        self.dismiss(self._id_to_name.get(item_id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

# ──────────────────────────────────────────────
# TUI — Screens
# ──────────────────────────────────────────────

class SetupScreen(Screen):
    """Step 1 — configure source, target, resolution, quality, presets."""

    BINDINGS = [
        Binding("r", "run", "Run"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._settings = SettingsManager()
        self._settings.load()
        self._last = self._settings.get_last_used()
        self._source_dir: str = self._last.get("source_dir", "")
        self._target_dir: str = self._last.get("target_dir", "")
        self._preset_params: Optional[dict] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer(id="setup-screen"):
            # ── Directories ──────────────────────────
            yield Label("Directories", classes="section-title")
            yield Horizontal(
                Label("Source: ", classes="dir-label"),
                Input(
                    value=self._source_dir,
                    placeholder="Press Enter to browse…",
                    id="source-input",
                    classes="dir-field",
                ),
                Button("Browse", id="btn-browse-source"),
                classes="dir-row",
            )
            yield Horizontal(
                Label("Target: ", classes="dir-label"),
                Input(
                    value=self._target_dir,
                    placeholder="Press Enter to browse…",
                    id="target-input",
                    classes="dir-field",
                ),
                Button("Browse", id="btn-browse-target"),
                classes="dir-row",
            )

            # ── Resolution Mode ──────────────────────
            yield Label("Resolution Mode", classes="section-title")
            with RadioSet(id="resolution-mode"):
                yield RadioButton("Fixed Dimensions", id="mode-fixed")
                yield RadioButton("Max Width or Height", id="mode-max", value=True)
                yield RadioButton("Percentage Scale", id="mode-pct")
                yield RadioButton("Named Preset", id="mode-preset")

            # Fixed inputs
            with Horizontal(id="inputs-fixed"):
                yield Input(value="1920", placeholder="Width", id="fixed-width", classes="num-input")
                yield Label(" × ")
                yield Input(value="1080", placeholder="Height", id="fixed-height", classes="num-input")
                with RadioSet(id="fixed-fit"):
                    yield RadioButton("Stretch", id="fit-stretch")
                    yield RadioButton("Letterbox", id="fit-letterbox", value=True)
                    yield RadioButton("Crop", id="fit-crop")

            # Max inputs
            with Horizontal(id="inputs-max"):
                yield Input(value="1280", placeholder="Max px", id="max-size", classes="num-input")
                with RadioSet(id="max-by"):
                    yield RadioButton("By Width", id="by-width")
                    yield RadioButton("By Height", id="by-height")
                    yield RadioButton("By Either", id="by-either", value=True)

            # Percentage inputs
            with Horizontal(id="inputs-pct"):
                yield Input(value="100", placeholder="Percent", id="pct-value", classes="num-input")
                yield Label("%")

            # Preset inputs
            with Horizontal(id="inputs-preset"):
                yield Label("Preset: ")
                yield Input(placeholder="Load a preset first", id="preset-display", disabled=True)

            # ── Quality (placeholder — Task 9) ───────
            yield Label("Quality", classes="section-title")
            yield Label("(quality slider goes here)", id="quality-placeholder")

            # ── Presets (placeholder — Task 9) ───────
            yield Label("Presets", classes="section-title")
            yield Label("(preset controls go here)", id="preset-placeholder")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-browse-source":
            self.app.push_screen(
                DirectoryModal(self._source_dir),
                callback=self._on_source_selected,
            )
        elif event.button.id == "btn-browse-target":
            self.app.push_screen(
                DirectoryModal(self._target_dir),
                callback=self._on_target_selected,
            )

    def _on_source_selected(self, path: Optional[Path]) -> None:
        if path is not None:
            self._source_dir = str(path)
            self.query_one("#source-input", Input).value = self._source_dir

    def _on_target_selected(self, path: Optional[Path]) -> None:
        if path is not None:
            self._target_dir = str(path)
            self.query_one("#target-input", Input).value = self._target_dir

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "source-input":
            self.app.push_screen(
                DirectoryModal(event.value),
                callback=self._on_source_selected,
            )
        elif event.input.id == "target-input":
            self.app.push_screen(
                DirectoryModal(event.value),
                callback=self._on_target_selected,
            )

    def action_run(self) -> None:
        self.app.push_screen("processing")

    def action_quit(self) -> None:
        self.app.exit()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id != "resolution-mode":
            return
        mode_map = {
            "mode-fixed": "inputs-fixed",
            "mode-max": "inputs-max",
            "mode-pct": "inputs-pct",
            "mode-preset": "inputs-preset",
        }
        selected_id = event.pressed.id
        for btn_id, panel_id in mode_map.items():
            panel = self.query_one(f"#{panel_id}")
            panel.display = (btn_id == selected_id)

    def _safe_int(self, widget_id: str, default: int) -> int:
        try:
            return int(self.query_one(widget_id, Input).value)
        except (ValueError, TypeError):
            return default

    def _safe_float(self, widget_id: str, default: float) -> float:
        try:
            return float(self.query_one(widget_id, Input).value)
        except (ValueError, TypeError):
            return default

    def get_resolution_params(self) -> ResolutionParams:
        mode_radio = self.query_one("#resolution-mode", RadioSet)
        selected = mode_radio.pressed_button
        mode_id = selected.id if selected else "mode-max"

        if mode_id == "mode-fixed":
            width = self._safe_int("#fixed-width", 1920)
            height = self._safe_int("#fixed-height", 1080)
            fit_radio = self.query_one("#fixed-fit", RadioSet)
            fit_btn = fit_radio.pressed_button
            fit = (fit_btn.id or "fit-letterbox").replace("fit-", "") if fit_btn else "letterbox"
            return ResolutionParams(mode="fixed", width=width, height=height, fit=fit)

        if mode_id == "mode-max":
            size = self._safe_int("#max-size", 1280)
            by_radio = self.query_one("#max-by", RadioSet)
            by_btn = by_radio.pressed_button
            by = (by_btn.id or "by-either").replace("by-", "") if by_btn else "either"
            return ResolutionParams(mode="max", size=size, by=by)

        if mode_id == "mode-pct":
            pct = self._safe_float("#pct-value", 100.0)
            return ResolutionParams(mode="percentage", percent=pct)

        # mode-preset: use stored params from loaded preset
        stored = getattr(self, "_preset_params", None)
        if stored:
            return ResolutionParams.from_dict(stored)
        return ResolutionParams(mode="max", size=1280, by="either")


class ProcessingScreen(Screen):
    """Step 2 — shows progress while batch runs."""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("Processing Screen — coming in next task", id="placeholder")
        yield Footer()


class SummaryScreen(Screen):
    """Step 3 — shows results after batch completes."""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("Summary Screen — coming in next task", id="placeholder")
        yield Footer()


# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────

class ImageResizeApp(App):
    CSS = APP_CSS
    TITLE = "ImageResize"
    SCREENS = {
        "setup": SetupScreen,
        "processing": ProcessingScreen,
        "summary": SummaryScreen,
    }

    def on_mount(self) -> None:
        self.push_screen("setup")

# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app = ImageResizeApp()
    app.run()
