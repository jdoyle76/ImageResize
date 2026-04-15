import json
import pytest
from pathlib import Path
from ImageResize import SettingsManager


@pytest.fixture
def tmp_settings(tmp_path):
    """Return a SettingsManager pointed at a temp file."""
    return SettingsManager(settings_path=tmp_path / "settings.json")


def test_load_returns_defaults_when_file_missing(tmp_settings):
    data = tmp_settings.load()
    assert "last_used" in data
    assert "presets" in data
    assert data["last_used"]["resolution_mode"] == "max"


def test_save_creates_file(tmp_settings):
    tmp_settings.load()
    tmp_settings.save()
    assert tmp_settings.settings_path.exists()


def test_save_and_reload_roundtrip(tmp_settings):
    tmp_settings.load()
    tmp_settings._data["last_used"]["quality"] = 42
    tmp_settings.save()

    fresh = SettingsManager(settings_path=tmp_settings.settings_path)
    fresh.load()
    assert fresh._data["last_used"]["quality"] == 42


def test_load_handles_corrupt_json(tmp_path):
    bad_file = tmp_path / "settings.json"
    bad_file.write_text("not json {{{{")
    sm = SettingsManager(settings_path=bad_file)
    data = sm.load()
    assert data["last_used"]["resolution_mode"] == "max"


def test_save_is_atomic(tmp_settings, tmp_path):
    """save() must write via .tmp then rename — no .tmp file left after save."""
    tmp_settings.load()
    tmp_settings.save()
    tmp_file = tmp_settings.settings_path.with_suffix(".tmp")
    assert not tmp_file.exists()
