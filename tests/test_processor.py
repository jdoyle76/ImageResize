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


# ── Resolution transforms ──────────────────────────────────────────────────

from PIL import Image as PILImage


def make_image(w: int, h: int, color: str = "red") -> PILImage.Image:
    return PILImage.new("RGB", (w, h), color)


# Fixed — stretch
def test_fixed_stretch_exact_dimensions(proc):
    img = make_image(800, 600)
    params = ResolutionParams(mode="fixed", width=400, height=300, fit="stretch")
    result = proc.apply_resolution(img, params)
    assert result.size == (400, 300)


def test_fixed_stretch_non_proportional(proc):
    img = make_image(800, 600)
    params = ResolutionParams(mode="fixed", width=200, height=400, fit="stretch")
    result = proc.apply_resolution(img, params)
    assert result.size == (200, 400)


# Fixed — letterbox
def test_fixed_letterbox_output_is_exact_target_size(proc):
    img = make_image(800, 600)
    params = ResolutionParams(mode="fixed", width=400, height=400, fit="letterbox")
    result = proc.apply_resolution(img, params)
    assert result.size == (400, 400)


# Fixed — crop
def test_fixed_crop_output_is_exact_target_size(proc):
    img = make_image(800, 600)
    params = ResolutionParams(mode="fixed", width=300, height=300, fit="crop")
    result = proc.apply_resolution(img, params)
    assert result.size == (300, 300)


# Max — by either
def test_max_by_either_scales_down(proc):
    img = make_image(2000, 1000)
    params = ResolutionParams(mode="max", size=1000, by="either")
    result = proc.apply_resolution(img, params)
    assert result.width <= 1000 and result.height <= 1000


def test_max_by_either_preserves_aspect_ratio(proc):
    img = make_image(2000, 1000)
    params = ResolutionParams(mode="max", size=1000, by="either")
    result = proc.apply_resolution(img, params)
    assert result.size == (1000, 500)


def test_max_does_not_upscale(proc):
    img = make_image(400, 300)
    params = ResolutionParams(mode="max", size=1000, by="either")
    result = proc.apply_resolution(img, params)
    assert result.size == (400, 300)


def test_max_by_width(proc):
    img = make_image(2000, 1000)
    params = ResolutionParams(mode="max", size=800, by="width")
    result = proc.apply_resolution(img, params)
    assert result.width == 800
    assert result.height == 400


def test_max_by_height(proc):
    img = make_image(2000, 1000)
    params = ResolutionParams(mode="max", size=500, by="height")
    result = proc.apply_resolution(img, params)
    assert result.height == 500
    assert result.width == 1000


# Percentage
def test_percentage_50_halves_dimensions(proc):
    img = make_image(800, 600)
    params = ResolutionParams(mode="percentage", percent=50.0)
    result = proc.apply_resolution(img, params)
    assert result.size == (400, 300)


def test_percentage_200_doubles_dimensions(proc):
    img = make_image(400, 300)
    params = ResolutionParams(mode="percentage", percent=200.0)
    result = proc.apply_resolution(img, params)
    assert result.size == (800, 600)
