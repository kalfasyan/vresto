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

    # Example 1: Find products from a specific date using contains
    logger.info("1. Find all products acquired on 2024-01-15 (using contains):")
    products = catalog.search_products_by_name(
        "20240115",
        match_type="contains",
        max_results=5,
    )
    if products:
        for i, product in enumerate(products, 1):
            logger.info(f"   {i}. {product.name}")
    else:
        logger.info("   No products found")

    # Example 2: Find products by mission identifier using startswith
    logger.info("\n2. Find all Sentinel-2A products (using startswith):")
    products = catalog.search_products_by_name(
        "S2A_",
        match_type="startswith",
        max_results=5,
    )
    if products:
        for i, product in enumerate(products, 1):
            logger.info(f"   {i}. {product.name}")
    else:
        logger.info("   No products found")

    # Example 3: Find level-2 processing products using contains
    logger.info("\n3. Find all Level-2A products (using contains):")
    products = catalog.search_products_by_name(
        "MSIL2A",
        match_type="contains",
        max_results=5,
    )
    if products:
        for i, product in enumerate(products, 1):
            logger.info(f"   {i}. {product.name}")
    else:
        logger.info("   No products found")

    # Example 4: Find a specific product using exact match
    logger.info("\n4. Find a specific product (using exact match):")
    # Note: This assumes a product with this exact name exists
    products = catalog.search_products_by_name(
        "S2B_MSIL2A_20231101T101239_N0509_R065_T32UPD_20231101T102345.SAFE",
        match_type="eq",
    )
    if products:
        product = products[0]
        logger.info(f"   Found: {product.name}")
        logger.info(f"   Collection: {product.collection}")
        logger.info(f"   Date: {product.sensing_date}")
        logger.info(f"   Size: {product.size_mb:.2f} MB")
    else:
        logger.info("   Product not found (you can try with a different name)")

    # Example 5: Find products with endswith
    logger.info("\n5. Find products ending with specific pattern (using endswith):")
    products = catalog.search_products_by_name(
        ".SAFE",
        match_type="endswith",
        max_results=5,
    )
    if products:
        logger.info(f"   Found {len(products)} products:")
        for i, product in enumerate(products[:3], 1):  # Show first 3
            logger.info(f"   {i}. {product.name}")
        if len(products) > 3:
            logger.info(f"   ... and {len(products) - 3} more")
    else:
        logger.info("   No products found")


if __name__ == "__main__":
    main()
