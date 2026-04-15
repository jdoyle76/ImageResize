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

# ──────────────────────────────────────────────
# TUI — Modals
# ──────────────────────────────────────────────

# DirectoryModal, PresetNameModal, PresetSelectModal go here

# ──────────────────────────────────────────────
# TUI — Screens
# ──────────────────────────────────────────────

# SetupScreen, ProcessingScreen, SummaryScreen go here

# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────

# ImageResizeApp goes here

# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app = ImageResizeApp()
    app.run()
