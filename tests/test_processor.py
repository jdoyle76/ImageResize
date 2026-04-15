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


# ── Output path and collision ──────────────────────────────────────────────

from pathlib import Path


def test_get_output_path_mirrors_structure(proc, tmp_path):
    source_root = tmp_path / "src"
    target_root = tmp_path / "out"
    source_file = source_root / "vacation" / "beach.jpg"
    result = proc._get_output_path(source_file, source_root, target_root)
    assert result == target_root / "vacation" / "beach.jpg"


def test_resolve_collision_no_conflict(proc, tmp_path):
    path = tmp_path / "photo.jpg"
    result = proc._resolve_collision(path)
    assert result == path


def test_resolve_collision_one_existing(proc, tmp_path):
    path = tmp_path / "photo.jpg"
    path.touch()
    result = proc._resolve_collision(path)
    assert result == tmp_path / "photo_1.jpg"


def test_resolve_collision_multiple_existing(proc, tmp_path):
    for name in ("photo.jpg", "photo_1.jpg", "photo_2.jpg"):
        (tmp_path / name).touch()
    result = proc._resolve_collision(tmp_path / "photo.jpg")
    assert result == tmp_path / "photo_3.jpg"


# ── Batch processing ───────────────────────────────────────────────────────

def make_test_image_file(path: Path, size=(100, 100)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = PILImage.new("RGB", size, "blue")
    img.save(path)


def test_batch_processes_jpeg_images(proc, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    make_test_image_file(src / "a.jpg")
    make_test_image_file(src / "b.jpg")

    params = ResolutionParams(mode="percentage", percent=50.0)
    result = proc.process_batch(src, dst, params, quality=80)

    assert result.processed == 2
    assert result.skipped == 0
    assert result.failed == 0


def test_batch_skips_non_images(proc, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    make_test_image_file(src / "real.jpg")
    (src / "readme.txt").write_text("not an image")

    params = ResolutionParams(mode="percentage", percent=100.0)
    result = proc.process_batch(src, dst, params, quality=80)

    assert result.processed == 1
    assert result.skipped == 1


def test_batch_preserves_directory_structure(proc, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    make_test_image_file(src / "sub" / "deep.jpg")

    params = ResolutionParams(mode="percentage", percent=100.0)
    proc.process_batch(src, dst, params, quality=80)

    assert (dst / "sub" / "deep.jpg").exists()


def test_batch_renames_on_collision(proc, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    make_test_image_file(src / "photo.jpg")
    # Pre-create a collision in dst
    dst.mkdir(parents=True)
    make_test_image_file(dst / "photo.jpg")

    params = ResolutionParams(mode="percentage", percent=100.0)
    result = proc.process_batch(src, dst, params, quality=80)

    assert result.renamed == 1
    assert (dst / "photo_1.jpg").exists()


def test_batch_cancel_stops_processing(proc, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for i in range(10):
        make_test_image_file(src / f"img{i}.jpg")

    progress_calls = []

    def on_progress(done, total, name):
        progress_calls.append(done)
        if done == 1:
            proc.cancel()

    params = ResolutionParams(mode="percentage", percent=100.0)
    result = proc.process_batch(src, dst, params, quality=80, progress_callback=on_progress)

    assert result.cancelled is True
    assert result.processed < 10


def test_batch_calls_progress_callback(proc, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    make_test_image_file(src / "a.jpg")
    make_test_image_file(src / "b.jpg")

    calls = []
    params = ResolutionParams(mode="percentage", percent=100.0)
    proc.process_batch(src, dst, params, quality=80, progress_callback=lambda d, t, n: calls.append((d, t, n)))

    assert len(calls) > 0
    assert calls[-1][0] == calls[-1][1]  # final call: done == total
