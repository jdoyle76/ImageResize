#!/usr/bin/env python3
"""ImageResize — Batch image resizing and quality adjustment TUI."""

# ──────────────────────────────────────────────
# Standard library
# ──────────────────────────────────────────────
from __future__ import annotations

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
    Slider,
    Static,
)
from textual.worker import Worker, get_current_worker

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────

# SettingsManager class goes here

# ──────────────────────────────────────────────
# Processing engine
# ──────────────────────────────────────────────

# ResolutionParams, ProcessResult, ImageProcessor go here

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
