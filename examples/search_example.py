"""Example usage of the Copernicus API for searching products."""

from loguru import logger

from vresto.api import BoundingBox, CatalogSearch, CopernicusConfig


def main():
    """Demonstrate catalog search functionality."""
    # Setup - credentials from environment variables
    # Set COPERNICUS_USERNAME and COPERNICUS_PASSWORD before running
    config = CopernicusConfig()

    if not config.validate():
        logger.error("Please set COPERNICUS_USERNAME and COPERNICUS_PASSWORD environment variables")
        logger.info("You can get credentials at: https://dataspace.copernicus.eu/")
        return

    # Initialize catalog search
    catalog = CatalogSearch(config=config)

    # Define search area (example: area around Leuven, Belgium)
    bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)

    # Search for Sentinel-2 products
    logger.info("Searching for Sentinel-2 products...")
    products = catalog.search_products(
        bbox=bbox,
        start_date="2024-01-01",
        end_date="2024-01-07",
        collection="SENTINEL-2",
        max_cloud_cover=20,
        max_results=5,
    )

    # Display results
    if not products:
        logger.warning("No products found")
    else:
        logger.info(f"Found {len(products)} products:")
        for i, product in enumerate(products, 1):
            logger.info(f"\n{i}. {product.name}")
            logger.info(f"   Collection: {product.collection}")
            logger.info(f"   Date: {product.sensing_date}")
            logger.info(f"   Size: {product.size_mb:.2f} MB")
            if product.cloud_cover is not None:
                logger.info(f"   Cloud Cover: {product.cloud_cover:.1f}%")
            if product.s3_path:
                logger.info(f"   S3 Path: {product.s3_path}")


if __name__ == "__main__":
    main()
