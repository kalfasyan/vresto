"""Example: Using the Products Module to View Quicklooks and Metadata.

This example demonstrates how to use the ProductsManager to download and
display product quicklooks and metadata for Sentinel-2 data.
"""

from vresto.api.catalog import ProductInfo
from vresto.products import ProductsManager


def example_quicklook():
    """Example: Download and view a product quicklook."""
    # Create a product info object (normally from catalog search)
    product = ProductInfo(
        id="S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207",
        name="S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207",
        collection="SENTINEL-2",
        sensing_date="2020-12-12",
        size_mb=1234.5,
        s3_path="s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE/",
        cloud_cover=15.5,
    )

    # Initialize the products manager
    manager = ProductsManager()

    # Download quicklook
    quicklook = manager.get_quicklook(product)

    if quicklook:
        # Save to file
        from pathlib import Path

        output_path = Path("./quicklook.jpg")
        quicklook.save_to_file(output_path)
        print(f"Quicklook saved to {output_path}")

        # Or get as base64 for web display
        base64_data = quicklook.get_base64()
        html_img = f'<img src="data:image/jpeg;base64,{base64_data}" />'
        print(f"Can embed in HTML: {html_img[:50]}...")


def example_metadata():
    """Example: Download and view product metadata."""
    product = ProductInfo(
        id="S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207",
        name="S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207",
        collection="SENTINEL-2",
        sensing_date="2020-12-12",
        size_mb=1234.5,
        s3_path="s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE/",
    )

    manager = ProductsManager()

    # Download metadata
    metadata = manager.get_metadata(product)

    if metadata:
        # Save to file
        from pathlib import Path

        output_path = Path("./metadata.xml")
        metadata.save_to_file(output_path)
        print(f"Metadata saved to {output_path}")

        # Or print first 500 characters
        print("Metadata content (first 500 chars):")
        print(metadata.metadata_xml[:500])


def example_batch_operations():
    """Example: Download quicklooks and metadata for multiple products."""
    products = [
        ProductInfo(
            id="S2A_PRODUCT_1",
            name="S2A_MSIL2A_20201201T000000_N0500_R001_T01ABC_20230101T000000",
            collection="SENTINEL-2",
            sensing_date="2020-12-01",
            size_mb=1000.0,
            s3_path="s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/01/S2A_MSIL2A_20201201T000000_N0500_R001_T01ABC_20230101T000000.SAFE/",
        ),
        ProductInfo(
            id="S2B_PRODUCT_2",
            name="S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207",
            collection="SENTINEL-2",
            sensing_date="2020-12-12",
            size_mb=1234.5,
            s3_path="s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE/",
        ),
    ]

    manager = ProductsManager()

    # Download all quicklooks
    quicklooks = manager.batch_get_quicklooks(products)
    print(f"\nDownloaded quicklooks for {sum(1 for q in quicklooks.values() if q)} products")

    # Download all metadata
    metadatas = manager.batch_get_metadata(products)
    print(f"Downloaded metadata for {sum(1 for m in metadatas.values() if m)} products")


if __name__ == "__main__":
    print("Products Module Examples")
    print("=" * 50)

    try:
        print("\n1. Downloading Quicklook...")
        example_quicklook()

        print("\n2. Downloading Metadata...")
        example_metadata()

        print("\n3. Batch Operations...")
        example_batch_operations()

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure Copernicus credentials are set in environment variables:")
        print("  - COPERNICUS_USERNAME")
        print("  - COPERNICUS_PASSWORD")
