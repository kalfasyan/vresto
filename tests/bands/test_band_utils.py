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


def test_compute_preview_shape_keeps_aspect_and_caps_max_dim():
    out_h, out_w = BandPreviewResizer.compute_preview_shape(1000, 500, 200)
    assert max(out_h, out_w) <= 200
    # Aspect ratio preserved within a 1-pixel rounding tolerance.
    assert abs((out_h / out_w) - 2.0) < 0.05


def test_compute_preview_shape_does_not_upscale():
    # Image already smaller than max_dim — must return original dims.
    assert BandPreviewResizer.compute_preview_shape(50, 80, 200) == (50, 80)


def test_scl_to_rgb_rejects_non_2d():
    arr = np.zeros((3, 4, 5), dtype=int)
    with pytest.raises(ValueError, match="2D"):
        SclProcessor.to_rgb(arr)


def test_scl_to_rgb_clips_out_of_range_values():
    # Values outside 0..11 should be clipped, not crash.
    arr = np.array([[-5, 99], [11, 0]], dtype=int)
    rgb = SclProcessor.to_rgb(arr)
    pal = SclProcessor.palette()

    assert rgb.shape == (2, 2, 3)
    # -5 clips to 0, 99 clips to 11
    assert (rgb[0, 0] == pal[0]).all()
    assert (rgb[0, 1] == pal[11]).all()


def test_resize_array_rejects_unsupported_ndim():
    arr = np.zeros((2, 3, 4, 5), dtype=np.uint8)
    with pytest.raises(ValueError, match="2D or 3D"):
        BandPreviewResizer.resize_array(arr, max_dim=100)


def test_to_uint8_handles_float_with_nan():
    arr = np.array([[0.0, 0.5, float("nan")], [1.0, 2.0, -0.5]], dtype=np.float32)
    out = BandPreviewResizer._to_uint8(arr)

    assert out.dtype == np.uint8
    # NaN became 0, 0.5 → ~127, values are clipped to [0, 255]
    assert out[0, 2] == 0
    assert 120 <= out[0, 1] <= 135
    # >1.0 clips to 255, <0 clips to 0
    assert out[1, 1] == 255
    assert out[1, 2] == 0


def test_to_uint8_normalises_wide_range_integers():
    # Integer array with max > 255 must be min-max normalised to 0..255.
    arr = np.array([[100, 5000], [0, 10000]], dtype=np.int32)
    out = BandPreviewResizer._to_uint8(arr)

    assert out.dtype == np.uint8
    assert out.min() == 0
    assert out.max() == 255


def test_to_uint8_passes_through_small_integers():
    arr = np.array([[0, 10], [200, 255]], dtype=np.int32)
    out = BandPreviewResizer._to_uint8(arr)

    assert out.dtype == np.uint8
    # Values <= 255 are cast as-is (no normalisation).
    assert (out == arr.astype(np.uint8)).all()


def test_to_uint8_fallback_for_other_dtypes():
    # bool isn't floating or integer; falls through to the normalisation path.
    arr = np.array([[False, True], [True, False]], dtype=bool)
    out = BandPreviewResizer._to_uint8(arr)

    assert out.dtype == np.uint8
    # min→0, max→255 after normalisation.
    assert out.min() == 0
    assert out.max() == 255
