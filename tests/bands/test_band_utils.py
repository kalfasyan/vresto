import numpy as np
import pytest

from vresto.bands import BandPreviewResizer, SclProcessor


def test_scl_palette_and_labels():
    pal = SclProcessor.palette()
    labels = SclProcessor.labels()
    assert pal.shape == (12, 3)
    assert pal.dtype == np.uint8
    assert len(labels) == 12


def test_scl_to_rgb_mapping_basic():
    # create a small SCL array with values 0..11
    arr = np.arange(12, dtype=int).reshape((3, 4)) % 12
    rgb = SclProcessor.to_rgb(arr)
    assert rgb.shape == (3, 4, 3)
    # values should match palette entries
    pal = SclProcessor.palette()
    for i in range(3):
        for j in range(4):
            assert (rgb[i, j] == pal[arr[i, j]]).all()


def test_resize_array_grayscale_and_rgb(tmp_path):
    # create a grayscale float array in 0..1
    arr = np.random.rand(500, 300).astype(np.float32)
    out = BandPreviewResizer.resize_array(arr, max_dim=200)
    assert out.ndim == 2
    assert max(out.shape) <= 200

    # create RGB uint8 array
    rgb = (np.random.rand(400, 600, 3) * 255).astype(np.uint8)
    out2 = BandPreviewResizer.resize_array(rgb, max_dim=300)
    assert out2.ndim == 3
    assert max(out2.shape[:2]) <= 300


def test_compute_preview_shape_validations():
    with pytest.raises(ValueError):
        BandPreviewResizer.compute_preview_shape(0, 100, 200)
    with pytest.raises(ValueError):
        BandPreviewResizer.compute_preview_shape(100, 0, 200)
    with pytest.raises(ValueError):
        BandPreviewResizer.compute_preview_shape(100, 100, 0)
