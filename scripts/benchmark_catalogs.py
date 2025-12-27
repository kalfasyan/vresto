import time

from vresto.api import CatalogSearch, CopernicusConfig


def benchmark_name_search(pattern: str, max_results: int = 20):
    """Benchmark name search performance across OData and STAC backends."""
    print(f"\n--- Benchmarking name search for pattern: '{pattern}' ---")

    # Test OData
    config_odata = CopernicusConfig(search_provider="odata")
    catalog_odata = CatalogSearch(config=config_odata)

    start_time = time.time()
    results_odata = catalog_odata.search_products_by_name(pattern, max_results=max_results)
    odata_duration = time.time() - start_time
    print(f"OData: Found {len(results_odata)} products in {odata_duration:.2f} seconds")

    # Test STAC with fallback
    config_stac = CopernicusConfig(search_provider="stac")
    catalog_stac = CatalogSearch(config=config_stac)

    start_time = time.time()
    results_stac_fallback = catalog_stac.search_products_by_name(pattern, max_results=max_results)
    stac_fallback_duration = time.time() - start_time
    print(f"STAC (w/ OData fallback): Found {len(results_stac_fallback)} products in {stac_fallback_duration:.2f} seconds")

    # Test STAC forced (this will show the slowness)
    print("\nForcing global STAC search (this may take a while)...")
    start_time = time.time()
    try:
        results_stac_forced = catalog_stac.search_products_by_name(pattern, max_results=max_results, force_stac=True)
        stac_forced_duration = time.time() - start_time
        print(f"STAC (forced global): Found {len(results_stac_forced)} products in {stac_forced_duration:.2f} seconds")
    except Exception as e:
        print(f"STAC (forced global) failed or timed out: {e}")

    # To show why we have the fallback, let's look at the implementation note
    print("\nNote: STAC backend internally delegates name searches to OData because")
    print("global STAC searches across all collections are significantly slower on CDSE.")


if __name__ == "__main__":
    # Example pattern that should return multiple results
    test_pattern = "S2A_MSIL2A_202001"
    benchmark_name_search(test_pattern)
