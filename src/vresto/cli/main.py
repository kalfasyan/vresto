"""Main CLI entry point for vresto."""

from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from vresto.api import CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

app = typer.Typer(help="A beautiful CLI for searching and accessing Copernicus Sentinel satellite data")
console = Console()


def setup_logging():
    """Configure logging for CLI."""
    logger.remove()
    logger.add(
        lambda msg: console.print(msg, highlight=False),
        format="{message}",
        level="INFO",
    )


@app.command()
def search_name(
    pattern: str = typer.Argument(..., help="Product name pattern to search for (e.g., 'S2A_MSIL2A', 'S2B_MSIL2A_202412')"),
    match_type: str = typer.Option("contains", help="Type of matching: contains, startswith, endswith, eq"),
    max_results: int = typer.Option(10, help="Maximum number of results to return"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Search for products by name.

    Examples:
        vresto search-name "S2A_MSIL2A"
        vresto search-name "S2B_MSIL2A_202412" --max-results 20
        vresto search-name "20241215" --match-type contains
    """
    setup_logging()

    try:
        config = CopernicusConfig()

        if not config.validate():
            console.print("[red]❌ Error: Copernicus credentials not configured[/red]")
            console.print("[yellow]Please set COPERNICUS_USERNAME and COPERNICUS_PASSWORD environment variables[/yellow]")
            console.print("[blue]Get free credentials at: https://dataspace.copernicus.eu/[/blue]")
            raise typer.Exit(code=1)

        catalog = CatalogSearch(config=config)

        console.print(f"[blue]🔍 Searching for products matching: '{pattern}'[/blue]")
        products = catalog.search_products_by_name(pattern, match_type=match_type, max_results=max_results)

        if not products:
            console.print(f"[yellow]⚠️  No products found matching '{pattern}'[/yellow]")
            return

        console.print(f"[green]✅ Found {len(products)} product(s)[/green]\n")

        # Display results in a table
        table = Table(title="Search Results")
        table.add_column("Product Name", style="cyan")
        table.add_column("Collection", style="magenta")
        table.add_column("Date", style="green")
        table.add_column("Size (MB)", style="yellow", justify="right")
        if any(p.cloud_cover is not None for p in products):
            table.add_column("Cloud %", style="blue", justify="right")

        for product in products:
            row = [
                product.display_name,
                product.collection,
                product.sensing_date,
                f"{product.size_mb:.2f}",
            ]
            if any(p.cloud_cover is not None for p in products):
                cloud_str = f"{product.cloud_cover:.1f}" if product.cloud_cover is not None else "N/A"
                row.append(cloud_str)
            table.add_row(*row)

        console.print(table)

        if verbose:
            console.print("\n[bold]Detailed Information:[/bold]")
            for i, product in enumerate(products, 1):
                console.print(f"\n[cyan]{i}. {product.name}[/cyan]")
                console.print(f"   ID: {product.id}")
                console.print(f"   Collection: {product.collection}")
                console.print(f"   Date: {product.sensing_date}")
                console.print(f"   Size: {product.size_mb:.2f} MB")
                if product.cloud_cover is not None:
                    console.print(f"   Cloud Cover: {product.cloud_cover}%")
                if product.s3_path:
                    console.print(f"   S3 Path: {product.s3_path}")
        else:
            console.print("\n[dim]Tip: Use [bold]-v[/bold] or [bold]--verbose[/bold] flag to see S3 paths for AWS CLI access[/dim]")

    except Exception as e:
        console.print(f"[red]❌ Error: {str(e)}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1)


@app.command()
def download_quicklook(
    product_name: str = typer.Argument(..., help="Product name to download quicklook for"),
    output: Path = typer.Option(Path.cwd(), "--output", "-o", help="Output directory for quicklook"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Download quicklook image for a product.

    Examples:
        vresto download-quicklook "S2A_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207"
        vresto download-quicklook "S2B_MSIL2A_20241215..." --output ./quicklooks
    """
    setup_logging()

    try:
        config = CopernicusConfig()

        if not config.validate():
            console.print("[red]❌ Error: Copernicus credentials not configured[/red]")
            raise typer.Exit(code=1)

        # First, get product info
        catalog = CatalogSearch(config=config)
        product_info = catalog.get_product_by_name(product_name)

        if not product_info:
            console.print(f"[yellow]⚠️  Product '{product_name}' not found in catalog[/yellow]")
            raise typer.Exit(code=1)

        console.print(f"[blue]📥 Downloading quicklook for {product_info.display_name}[/blue]")

        manager = ProductsManager(config=config)
        quicklook = manager.get_quicklook(product_info)

        if quicklook:
            output.mkdir(parents=True, exist_ok=True)
            output_file = output / f"{product_info.display_name}-ql.jpg"
            quicklook.save_to_file(output_file)
            console.print(f"[green]✅ Quicklook saved to: {output_file}[/green]")
        else:
            console.print("[yellow]⚠️  Could not download quicklook[/yellow]")
            raise typer.Exit(code=1)

    except Exception as e:
        console.print(f"[red]❌ Error: {str(e)}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1)


@app.command()
def download_metadata(
    product_name: str = typer.Argument(..., help="Product name to download metadata for"),
    output: Path = typer.Option(Path.cwd(), "--output", "-o", help="Output directory for metadata"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Download metadata XML for a product.

    Examples:
        vresto download-metadata "S2A_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207"
        vresto download-metadata "S2B_MSIL2A_20241215..." --output ./metadata
    """
    setup_logging()

    try:
        config = CopernicusConfig()

        if not config.validate():
            console.print("[red]❌ Error: Copernicus credentials not configured[/red]")
            raise typer.Exit(code=1)

        # First, get product info
        catalog = CatalogSearch(config=config)
        product_info = catalog.get_product_by_name(product_name)

        if not product_info:
            console.print(f"[yellow]⚠️  Product '{product_name}' not found in catalog[/yellow]")
            raise typer.Exit(code=1)

        console.print(f"[blue]📥 Downloading metadata for {product_info.display_name}[/blue]")

        manager = ProductsManager(config=config)
        metadata = manager.get_metadata(product_info)

        if metadata:
            output.mkdir(parents=True, exist_ok=True)
            output_file = output / f"{product_info.display_name}-metadata.xml"
            metadata.save_to_file(output_file)
            console.print(f"[green]✅ Metadata saved to: {output_file}[/green]")
        else:
            console.print("[yellow]⚠️  Could not download metadata[/yellow]")
            raise typer.Exit(code=1)

    except Exception as e:
        console.print(f"[red]❌ Error: {str(e)}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1)


@app.command()
def download_bands(
    product_name: str = typer.Argument(..., help="Product identifier (short name, S3 path, or .SAFE directory)"),
    bands: str = typer.Argument(..., help="Bands to download, comma-separated (e.g., 'B02,B03,B04')"),
    output: Path = typer.Option(Path.cwd() / "data", "--output", "-o", help="Output directory"),
    resolution: str = typer.Option("native", help="Target resolution: 10, 20, 60, or 'native'"),
    resample: bool = typer.Option(False, "--resample", help="Resample bands to target resolution"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files"),
    no_preserve_structure: bool = typer.Option(False, "--no-preserve-structure", help="Don't preserve S3 directory structure"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Download specific bands from a product.

    Examples:
        vresto download-bands "S2A_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207" "B02,B03,B04"
        vresto download-bands "S2B_MSIL2A_20241215..." "B04,B03,B02" --resolution 10
        vresto download-bands "S2A_..." "TCI" --output ./quicklook_data
    """
    setup_logging()

    try:
        config = CopernicusConfig()

        if not config.validate():
            console.print("[red]❌ Error: Copernicus credentials not configured[/red]")
            raise typer.Exit(code=1)

        # Parse bands
        band_list = [b.strip() for b in bands.split(",")]

        console.print(f"[blue]📥 Downloading bands {band_list} from {product_name}[/blue]")

        manager = ProductsManager(config=config)

        # Convert resolution string to int if needed
        resolution_int = resolution if resolution == "native" else int(resolution)

        files = manager.download_product_bands(
            product=product_name,
            bands=band_list,
            resolution=resolution_int,
            dest_dir=output,
            resample=resample,
            overwrite=overwrite,
            preserve_s3_structure=not no_preserve_structure,
        )

        if files:
            console.print(f"[green]✅ Downloaded {len(files)} file(s)[/green]")
            if verbose:
                for file in files:
                    console.print(f"   ✓ {file}")
        else:
            console.print("[yellow]⚠️  No files downloaded[/yellow]")
            raise typer.Exit(code=1)

    except Exception as e:
        console.print(f"[red]❌ Error: {str(e)}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1)


@app.command()
def validate_credentials(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Validate Copernicus credentials.

    Example:
        vresto validate-credentials
    """
    setup_logging()

    try:
        config = CopernicusConfig()

        if config.validate():
            console.print("[green]✅ Credentials are valid[/green]")
            if verbose:
                console.print(f"   Username: {config.username}")
        else:
            console.print("[red]❌ Credentials are not configured[/red]")
            console.print("[yellow]Please set COPERNICUS_USERNAME and COPERNICUS_PASSWORD environment variables[/yellow]")
            raise typer.Exit(code=1)

    except Exception as e:
        console.print(f"[red]❌ Error: {str(e)}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1)


def main():
    """Main entry point for vresto CLI."""
    app()


if __name__ == "__main__":
    main()
