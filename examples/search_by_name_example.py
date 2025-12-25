"""Example usage of the product name search functionality."""

from loguru import logger

from vresto.api import CatalogSearch, CopernicusConfig


def main():
    """Demonstrate product name search functionality."""
    # Setup - credentials from environment variables
    config = CopernicusConfig()

    if not config.validate():
        logger.error("Please set COPERNICUS_USERNAME and COPERNICUS_PASSWORD environment variables")
        logger.info("You can get credentials at: https://dataspace.copernicus.eu/")
        return

    # Initialize catalog search
    catalog = CatalogSearch(config=config)

    logger.info("\n=== Product Name Search Examples ===\n")

    # Example 1: Find Sentinel-2A L2A products from December 2024
    logger.info("1. Find Sentinel-2A L2A products from December 2024 (using contains):")
    products = catalog.search_products_by_name(
        "S2A_MSIL2A_202412",
        match_type="contains",
        max_results=3,
    )
    if products:
        for i, product in enumerate(products, 1):
            logger.info(f"   {i}. {product.name}")
            logger.info(f"      Date: {product.sensing_date}, Size: {product.size_mb:.0f} MB")
    else:
        logger.info("   No products found")

    # Example 2: Find Sentinel-2B L2A products from November 2024
    logger.info("\n2. Find Sentinel-2B L2A products from November 2024 (using contains):")
    products = catalog.search_products_by_name(
        "S2B_MSIL2A_202411",
        match_type="contains",
        max_results=3,
    )
    if products:
        for i, product in enumerate(products, 1):
            logger.info(f"   {i}. {product.name}")
            logger.info(f"      Date: {product.sensing_date}, Size: {product.size_mb:.0f} MB")
    else:
        logger.info("   No products found")

    # Example 3: Find any Sentinel-2 product from a specific day
    logger.info("\n3. Find Sentinel-2 products from a specific day (using contains):")
    products = catalog.search_products_by_name(
        "20241215",
        match_type="contains",
        max_results=3,
    )
    if products:
        for i, product in enumerate(products, 1):
            logger.info(f"   {i}. {product.name}")
            logger.info(f"      Collection: {product.collection}, Date: {product.sensing_date}")
    else:
        logger.info("   No products found")


if __name__ == "__main__":
    main()
