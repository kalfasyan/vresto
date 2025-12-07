import boto3
import moto
import numpy as np
import pytest

from vresto.products.downloader import ProductDownloader, S3Mapper

mock_s3 = getattr(moto, "mock_s3", None) or getattr(moto, "mock_aws", None)


try:
    import rasterio

    has_rio = True
except Exception:
    has_rio = False


@mock_s3
def test_list_available_bands_and_build_keys():
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "test-bucket"
    s3.create_bucket(Bucket=bucket)
    # create a sample IMG_DATA structure
    base = "prefix/GRANULE/L2A_TILE/IMG_DATA/R10m/"
    files = [
        base + "TILE_B02_10m.jp2",
        base + "TILE_B03_10m.jp2",
    ]
    for f in files:
        s3.put_object(Bucket=bucket, Key=f, Body=b"123")

    mapper = S3Mapper(s3_client=s3)
    img_uri = f"s3://{bucket}/prefix/GRANULE/L2A_TILE/IMG_DATA/"
    bands = mapper.list_img_objects(img_uri)
    assert any("B02_10m.jp2" in k for k in bands)

    pd = ProductDownloader(s3_client=s3)
    avail = pd.list_available_bands(f"s3://{bucket}/prefix/")
    assert "B02" in avail and 10 in avail["B02"]

    keys = pd.build_keys_for_bands(f"s3://{bucket}/prefix/", ["B02"], 10)
    assert len(keys) == 1
    assert keys[0].startswith("s3://")


@pytest.mark.skipif(not has_rio, reason="rasterio not installed")
def test_resample_small_fixture(tmp_path):
    # create a tiny 2x2 raster with 10m resolution and resample to 20m
    data = (np.arange(4, dtype="uint8") + 1).reshape((1, 2, 2))
    meta = {
        "driver": "GTiff",
        "dtype": "uint8",
        "count": 1,
        "height": 2,
        "width": 2,
        "crs": "EPSG:32631",
        "transform": rasterio.transform.from_origin(0, 20, 10, 10),
    }
    src_file = tmp_path / "src.tif"
    with rasterio.open(src_file, "w", **meta) as dst:
        dst.write(data)

    pd = ProductDownloader(s3_client=None)
    out_file = tmp_path / "out_20m.tif"
    pd._resample_raster(src_file, out_file, 20, method="nearest")
    with rasterio.open(out_file) as r:
        assert r.width == 1 or r.height == 1


@mock_s3
def test_products_manager_wrapper(tmp_path):
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "test-bucket"
    s3.create_bucket(Bucket=bucket)
    base = "prefix/GRANULE/L2A_TILE/IMG_DATA/R10m/"
    key = base + "TILE_B02_10m.jp2"
    s3.put_object(Bucket=bucket, Key=key, Body=b"123")

    # Use ProductDownloader directly with mock s3 client to simulate manager behavior
    pd = ProductDownloader(s3_client=s3)

    class DummyProduct:
        name = "TILE"
        s3_path = f"s3://{bucket}/prefix/"

    keys = pd.build_keys_for_bands(DummyProduct.s3_path, ["B02"], 10)
    assert len(keys) == 1
    res = pd.download_product(DummyProduct.s3_path, ["B02"], 10, tmp_path, resample=False)
    assert len(res) == 1
