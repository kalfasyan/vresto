# Makefile for vresto package management

.PHONY: help bump-patch bump-minor bump-major release-patch release-minor release-major build test lint lint-fix format format-fix clean dev-install docs-build docs-serve publish version check-release

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Version bumping
bump-patch:  ## Bump patch version (0.1.14 -> 0.1.15)
	python3 scripts/bump_version.py patch

bump-minor:  ## Bump minor version (0.1.14 -> 0.2.0)
	python3 scripts/bump_version.py minor

bump-major:  ## Bump major version (0.1.14 -> 1.0.0)
	python3 scripts/bump_version.py major

# Release process
release-patch:  ## Bump patch version and create release
	./scripts/release.sh patch

release-minor:  ## Bump minor version and create release
	./scripts/release.sh minor

release-major:  ## Bump major version and create release
	./scripts/release.sh major

# Development

# UI/Web Interface
app:  ## Run the Sentinel Browser web interface
	uv run python src/vresto/ui/app.py

# Testing
test-parallel:  ## Run tests in parallel
	uv run --extra dev pytest -n auto tests/

test:  ## Run tests
	uv run --extra dev pytest tests/

lint:  ## Run linting
	uv run --extra dev ruff check .

lint-fix:  ## Run linting and auto-fix issues
	uv run --extra dev ruff check . --fix

format:  ## Check code formatting
	uv run --extra dev ruff format --preview --check .

format-fix:  ## Auto-format code
	uv run --extra dev ruff format --preview .

build:  ## Build package
	uv build

clean:  ## Clean build artifacts
	rm -rf dist/ build/ *.egg-info/

# Quick development tasks
dev-install:  ## Install package in development mode with dev dependencies
	uv sync --extra dev

docs-build:  ## Install docs dependencies and build the MkDocs documentation site
	uv sync --extra docs
	uv run --extra docs mkdocs build -f mkdocs.yml

docs-serve:  ## Install docs dependencies and serve the MkDocs site locally at http://127.0.0.1:8000
	uv sync --extra docs
	# Use uv run so the correct project venv and deps are used
	uv run --extra docs mkdocs serve -f mkdocs.yml

publish:  ## Publish to PyPI (manual - normally done by GitHub Actions)
	@echo "⚠️  Note: Publishing is normally automated via GitHub Actions"
	@echo "🚀 To publish: git tag vX.Y.Z && git push --tags"
	@echo ""
	@echo "🔄 Manual publish (not recommended):"
	uv publish

# Show current version
version:  ## Show current version
	@python3 -c "import sys; sys.path.insert(0, 'src'); from vresto._version import __version__; print(f'Current version: {__version__}')"

# Check release status
check-release:  ## Check if current version is published
	@uv run python3 -c "import sys, requests; sys.path.insert(0, 'src'); from vresto._version import __version__; r=requests.get(f'https://pypi.org/pypi/vresto/{__version__}/json'); print(f'✅ Version {__version__} is published' if r.status_code==200 else f'❌ Version {__version__} not found on PyPI')"
