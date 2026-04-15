import pytest
from ImageResize import ImageProcessor, ResolutionParams


@pytest.fixture
def proc():
    return ImageProcessor()


# ── Quality mapping ────────────────────────────────────────────────────────

def test_map_quality_jpeg_100_pct(proc):
    result = proc.map_quality(100, "JPEG")
    assert result == {"quality": 95}


def test_map_quality_jpeg_0_pct(proc):
    result = proc.map_quality(0, "JPEG")
    assert result == {"quality": 1}


def test_map_quality_jpeg_50_pct(proc):
    result = proc.map_quality(50, "JPEG")
    assert result["quality"] == pytest.approx(47, abs=2)


def test_map_quality_png_100_pct(proc):
    # 100% quality → 0 compression
    result = proc.map_quality(100, "PNG")
    assert result == {"compress_level": 0}


def test_map_quality_png_0_pct(proc):
    # 0% quality → 9 compression
    result = proc.map_quality(0, "PNG")
    assert result == {"compress_level": 9}


def test_map_quality_webp(proc):
    result = proc.map_quality(75, "WEBP")
    assert result == {"quality": 75}


def test_map_quality_unknown_format(proc):
    result = proc.map_quality(80, "BMP")
    assert result == {}


def test_map_quality_jpeg_case_insensitive(proc):
    assert proc.map_quality(100, "jpeg") == proc.map_quality(100, "JPEG")
