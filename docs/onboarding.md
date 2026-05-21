# ONBOARDING

This document gives external engineers a fast, high-level understanding of `vresto`: what it is, how it is structured, and why it is built this way.

## Project Scope in One Minute

`vresto` is a Python application for discovering, previewing, and downloading Copernicus Sentinel products.

It supports two user styles:
- Interactive exploration through a NiceGUI-based web interface.
- Programmatic and scripted workflows through a Python API and CLI.

The project focuses on practical geospatial workflows:
- Search products quickly.
- Inspect metadata and quicklooks.
- Download full products or selected bands.
- Visualize high-resolution bands via local tile serving.

## Setup Philosophy

The project is designed to run in three common modes:
- Docker Compose: fastest way to run the full app with minimal local setup.
- Pixi/uv local development: reproducible Python environment for contributors.
- PyPI installation: simple consumer install for API/CLI usage.

The default contributor flow is environment-first and reproducible:
- Use `pixi run ...` for project commands.
- Keep credentials in environment variables or `.env` (never committed).
- Use Make targets and existing scripts to avoid ad-hoc setup drift.

## Architecture at a Glance

The codebase is organized by responsibility under `src/vresto/`:

- `api/`: external service communication and search abstractions.
- `products/`: download and product-level workflows.
- `bands/`: band I/O, composition, and image-oriented operations.
- `services/`: specialized service integrations (for example tiles/worldcover/lcm).
- `ui/`: NiceGUI app, map interface, and widgets.
- `cli/`: Typer-based command-line entry points.

The architectural shape is layered:
1. Entry points (`vresto`, `vresto-cli`) orchestrate user intent.
2. UI/CLI call domain modules (`api`, `products`, `bands`, `services`).
3. Domain modules isolate external systems and file/network behavior.

This separation keeps UI concerns out of core logic and allows the same capabilities to be reused by both API and CLI flows.

## Why These Decisions

Key design choices were made for operator usability and maintainability:

- Dual interface (UI + API/CLI): supports both exploratory and automated workflows.
- Docker support: lowers friction for first-time users and demos.
- Modular package layout: localizes complexity and reduces cross-module coupling.
- Local tile serving: enables high-resolution visual inspection without external GIS infrastructure.
- Scripted helpers in `scripts/`: operational tasks remain explicit and repeatable.
- Strong test coverage by domain in `tests/`: behavior is validated where logic lives.

## Repository Structure Expectations

At the top level:
- `src/`: production code.
- `tests/`: automated tests, grouped by feature/domain.
- `docs/`: MkDocs documentation.
- `scripts/`: utility and release/setup helpers.
- `docker-compose.yml` and `Dockerfile`: containerized runtime.
- `pyproject.toml`: dependencies, tooling, entry points, and test/lint config.

This is a conventional Python project layout so that external engineers can navigate quickly without project-specific conventions.

## How to Think About Changes

When proposing changes, align with these principles:
- Keep business logic out of UI widgets.
- Extend existing domain modules before adding new top-level abstractions.
- Prefer reproducible commands and documented workflows.
- Add or update tests with behavior changes.
- Keep docs synchronized with developer-facing changes.

## First Practical Steps for New Engineers

1. Run the app once (Docker or local) to understand the end-to-end flow.
2. Read the module matching your focus area (`api`, `products`, `bands`, `ui`).
3. Run tests and lint in the project environment.
4. Make a small scoped change and validate with targeted tests.

If you follow the module boundaries and existing tooling conventions, contributions stay easy to review and integrate.
