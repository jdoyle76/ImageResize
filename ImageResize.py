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
from textual.containers import Container, Horizontal, ScrollableContainer
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
from textual.css.query import NoMatches
from textual.message import Message
from textual.worker import get_current_worker

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
        if params.width is None or params.height is None:
            return img
        target = (params.width, params.height)
        if params.fit == "stretch":
            return img.resize(target, Image.Resampling.LANCZOS)
        if params.fit == "letterbox":
            img = img.copy()
            img.thumbnail(target, Image.Resampling.LANCZOS)
            fill = (0,) * len(img.getbands())
            result = Image.new(img.mode, target, fill)
            offset = ((target[0] - img.width) // 2, (target[1] - img.height) // 2)
            result.paste(img, offset)
            return result
        if params.fit == "crop":
            ratio = max(target[0] / img.width, target[1] / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
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
        return img.resize(new_size, Image.Resampling.LANCZOS)

    def _apply_percentage(self, img: Image.Image, params: ResolutionParams) -> Image.Image:
        ratio = params.percent / 100.0
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        return img.resize(new_size, Image.Resampling.LANCZOS)

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
# TUI — Messages
# ──────────────────────────────────────────────

class ProgressUpdate(Message):
    def __init__(self, done: int, total: int, current_file: str) -> None:
        super().__init__()
        self.done = done
        self.total = total
        self.current_file = current_file


class BatchComplete(Message):
    def __init__(self, result: "ProcessResult") -> None:
        super().__init__()
        self.result = result


class BatchError(Message):
    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message


class _HelpModal(ModalScreen):
    """Show keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "dismiss_none", "Close"),
        Binding("?", "dismiss_none", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(classes="modal-container"):
            yield Label("Keyboard Shortcuts", classes="section-title")
            yield Static(
                "  R       Run batch processing\n"
                "  Q       Quit\n"
                "  Tab     Next field\n"
                "  Enter   Browse directory (when in dir field)\n"
                "  ?       Show this help\n"
                "  Esc     Close modal / cancel"
            )
            yield Button("Close", variant="primary", id="btn-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class _ErrorModal(ModalScreen):
    """Display an unrecoverable error and return to Setup."""

    BINDINGS = [Binding("escape", "dismiss_none", "Close")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(classes="modal-container"):
            yield Label("Error", classes="section-title")
            yield Label(self._message)
            yield Button("Return to Setup", variant="primary", id="btn-ok")

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
        Binding("?", "help", "Help"),
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

            # ── Quality ──────────────────────────────
            yield Label("Quality", classes="section-title")
            with Horizontal(id="quality-row"):
                yield Label("Quality: 85%  [JPEG→80]", id="quality-label")
                yield Input(value="85", placeholder="0-100", id="quality-input", classes="num-input")

            # ── Presets ───────────────────────────────
            yield Label("Presets", classes="section-title")
            with Horizontal(id="preset-row"):
                yield Button("Save as Preset", id="btn-save-preset")
                yield Button("Load Preset", id="btn-load-preset")

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
        elif event.button.id == "btn-save-preset":
            self.app.push_screen(PresetNameModal(), callback=self._on_preset_name)
        elif event.button.id == "btn-load-preset":
            names = list(self._settings.get_presets().keys())
            if names:
                self.app.push_screen(
                    PresetSelectModal(names), callback=self._on_preset_loaded
                )
            else:
                self.notify("No saved presets yet.", title="Load Preset")

    def _on_source_selected(self, path: Optional[Path]) -> None:
        if path is not None:
            self._source_dir = str(path)
            self.query_one("#source-input", Input).value = self._source_dir

    def _on_target_selected(self, path: Optional[Path]) -> None:
        if path is not None:
            self._target_dir = str(path)
            self.query_one("#target-input", Input).value = self._target_dir

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "quality-input":
            return
        try:
            pct = max(0, min(100, int(event.value)))
        except (ValueError, TypeError):
            pct = 85
        jpeg_val = max(1, min(95, round(pct * 95 / 100)))
        self.query_one("#quality-label", Label).update(
            f"Quality: {pct}%  [JPEG→{jpeg_val}]"
        )

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
        source = self.query_one("#source-input", Input).value.strip()
        target = self.query_one("#target-input", Input).value.strip()

        if not source or not Path(source).is_dir():
            self.notify("Please select a valid source directory.", title="Error", severity="error")
            return
        if not target:
            self.notify("Please select a target directory.", title="Error", severity="error")
            return

        if Path(source).resolve() == Path(target).resolve():
            self.notify("Source and target must be different directories.", title="Error", severity="error")
            return

        quality = max(0, min(100, self._safe_int("#quality-input", 85)))
        params = self.get_resolution_params()

        # Save last-used
        self._settings.set_last_used({
            "source_dir": source,
            "target_dir": target,
            "resolution_mode": params.mode,
            "resolution_params": params.to_dict(),
            "quality": quality,
        })

        self.app.switch_screen(
            ProcessingScreen(source, target, params, quality)
        )

    def action_quit(self) -> None:
        self.app.exit()

    def action_help(self) -> None:
        self.app.push_screen(_HelpModal())

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

    def on_mount(self) -> None:
        last = self._last
        quality = last.get("quality", 85)
        self.query_one("#quality-input", Input).value = str(quality)

        mode = last.get("resolution_mode", "max")
        mode_btn_id = {
            "fixed": "mode-fixed",
            "max": "mode-max",
            "percentage": "mode-pct",
            "preset": "mode-preset",
        }.get(mode, "mode-max")
        self.query_one(f"#{mode_btn_id}", RadioButton).value = True

        params = last.get("resolution_params", {})
        if mode == "fixed":
            self.query_one("#fixed-width", Input).value = str(params.get("width", 1920))
            self.query_one("#fixed-height", Input).value = str(params.get("height", 1080))
            # Restore fit sub-param
            fit = params.get("fit", "letterbox")
            fit_btn_id = {"stretch": "fit-stretch", "letterbox": "fit-letterbox", "crop": "fit-crop"}.get(fit, "fit-letterbox")
            self.query_one(f"#{fit_btn_id}", RadioButton).value = True
        elif mode == "max":
            self.query_one("#max-size", Input).value = str(params.get("size", 1280))
            # Restore by sub-param
            by = params.get("by", "either")
            by_btn_id = {"width": "by-width", "height": "by-height", "either": "by-either"}.get(by, "by-either")
            self.query_one(f"#{by_btn_id}", RadioButton).value = True
        elif mode == "percentage":
            self.query_one("#pct-value", Input).value = str(params.get("percent", 100))

    def _on_preset_name(self, name: Optional[str]) -> None:
        if not name:
            return
        quality = self._safe_int("#quality-input", 85)
        params = self.get_resolution_params()
        settings = {
            "resolution_mode": params.mode,
            "resolution_params": params.to_dict(),
            "quality": quality,
        }
        self._settings.save_preset(name, settings)
        self.notify(f"Preset '{name}' saved.", title="Preset Saved")

    def _on_preset_loaded(self, name: Optional[str]) -> None:
        if not name:
            return
        preset = self._settings.get_presets().get(name, {})
        quality = preset.get("quality", 85)
        self.query_one("#quality-input", Input).value = str(quality)
        self._preset_params = preset.get("resolution_params", {})
        # Switch radio to "Named Preset" mode
        self.query_one("#mode-preset", RadioButton).value = True
        self.query_one("#preset-display", Input).value = name
        self.notify(f"Loaded preset '{name}'.", title="Preset Loaded")

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

    def __init__(
        self,
        source_dir: str,
        target_dir: str,
        resolution_params: "ResolutionParams",
        quality: int,
    ) -> None:
        super().__init__()
        self._source_dir = Path(source_dir)
        self._target_dir = Path(target_dir)
        self._resolution_params = resolution_params
        self._quality = quality
        self._processor = ImageProcessor()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="processing-screen"):
            yield Label(
                f"{self._source_dir} → {self._target_dir}",
                id="processing-header",
            )
            yield ProgressBar(total=100, show_eta=False, id="progress-bar")
            yield Label("Starting…", id="current-file")
            yield Label("0 / 0 files", id="file-counter")
            yield Button("Cancel", variant="error", id="btn-cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._run_batch, exclusive=True, thread=True)

    def _run_batch(self) -> None:
        worker = get_current_worker()
        try:
            if not self._target_dir.exists():
                self._target_dir.mkdir(parents=True)

            def on_progress(done: int, total: int, name: str) -> None:
                if worker.is_cancelled:
                    self._processor.cancel()
                    return
                self.post_message(ProgressUpdate(done, total, name))

            result = self._processor.process_batch(
                self._source_dir,
                self._target_dir,
                self._resolution_params,
                self._quality,
                progress_callback=on_progress,
            )
            self.post_message(BatchComplete(result))
        except PermissionError as exc:
            self.post_message(BatchError(f"Permission denied: {exc}"))
        except FileNotFoundError as exc:
            self.post_message(BatchError(f"Directory not found: {exc}"))
        except Exception as exc:
            self.post_message(BatchError(f"Unexpected error: {exc}"))

    def on_progress_update(self, event: ProgressUpdate) -> None:
        bar = self.query_one("#progress-bar", ProgressBar)
        if bar.total != event.total and event.total > 0:
            bar.total = event.total
        bar.progress = event.done
        self.query_one("#current-file", Label).update(
            f"Processing: {event.current_file}" if event.current_file else "Finishing…"
        )
        self.query_one("#file-counter", Label).update(
            f"{event.done} / {event.total} files"
        )

    def on_batch_complete(self, event: BatchComplete) -> None:
        self.app.switch_screen(SummaryScreen(event.result))

    def on_batch_error(self, event: BatchError) -> None:
        self.app.push_screen(
            _ErrorModal(event.message),
            callback=lambda _: self.app.switch_screen(SetupScreen()),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self._processor.cancel()
            self.query_one("#btn-cancel", Button).disabled = True
            self.query_one("#current-file", Label).update("Cancelling…")


class SummaryScreen(Screen):
    """Step 3 — shows results after batch completes."""

    BINDINGS = [
        Binding("e", "toggle_errors", "Toggle errors"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, result: "ProcessResult") -> None:
        super().__init__()
        self._result = result

    def compose(self) -> ComposeResult:
        r = self._result
        status = "Cancelled" if r.cancelled else "Complete"

        yield Header(show_clock=True)
        with Container(id="summary-screen"):
            yield Label(status, classes="section-title", id="summary-status")

            # Stats
            with Container(id="stats-block"):
                yield Horizontal(
                    Label("Processed successfully:", classes="stat-label"),
                    Label(str(r.processed), classes="stat-value"),
                    classes="stat-row",
                )
                yield Horizontal(
                    Label("Skipped (non-image):", classes="stat-label"),
                    Label(str(r.skipped), classes="stat-value"),
                    classes="stat-row",
                )
                yield Horizontal(
                    Label("Renamed (collision):", classes="stat-label"),
                    Label(str(r.renamed), classes="stat-value"),
                    classes="stat-row",
                )
                yield Horizontal(
                    Label("Failed:", classes="stat-label"),
                    Label(str(r.failed), classes="stat-value"),
                    classes="stat-row",
                )

            # Error log (only shown if there are errors)
            if r.errors:
                err_title = f"{len(r.errors)} error{'s' if len(r.errors) != 1 else ''}"
                with Collapsible(title=err_title, collapsed=True, id="error-log"):
                    for filename, reason in r.errors:
                        yield Label(f"[bold]{filename}[/bold]: {reason}")

            # Buttons
            with Horizontal(id="summary-buttons"):
                yield Button(
                    "Process Another Batch",
                    variant="primary",
                    id="btn-another",
                )
                yield Button("Quit", id="btn-quit")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-another":
            self.app.switch_screen(SetupScreen())
        elif event.button.id == "btn-quit":
            self.action_quit()

    def action_toggle_errors(self) -> None:
        try:
            log = self.query_one("#error-log", Collapsible)
            log.collapsed = not log.collapsed
        except NoMatches:
            pass

    def action_quit(self) -> None:
        self.app.exit()


# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────

class ImageResizeApp(App):
    CSS = APP_CSS
    TITLE = "ImageResize"

    def on_mount(self) -> None:
        self.push_screen(SetupScreen())

# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app = ImageResizeApp()
    app.run()
