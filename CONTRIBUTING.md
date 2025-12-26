# Contributing to vresto

Thank you for your interest in contributing! This project values kind communication, understanding, and respect. Whether you're fixing bugs, improving documentation, or suggesting new features, your contributions are welcome.

## How to Contribute

- **Open Communication:** Please discuss any major changes or ideas in an issue before making a pull request. This helps ensure your work aligns with the project's goals.
- **Respect:** Be kind and constructive in all interactions.
- **Transparency:** Be clear about what your change does and why. Include context and reasoning in issues and pull requests.

## Submitting Issues

- Provide as much detail as possible (steps to reproduce, environment, etc.).

## Submitting Pull Requests

### Keep PRs Small and Focused

We strongly encourage **small, focused pull requests** that address a single well-defined problem. This makes reviews faster, easier to understand, and less likely to introduce unexpected side effects.

- **Target size:** Aim for a soft threshold of **~500 lines of changes maximum**. If your PR is significantly larger, consider breaking it into smaller, logically independent PRs.
- **One problem per PR:** Each PR should solve one problem or implement one feature. Avoid mixing unrelated changes (e.g., refactoring + new feature).
- **Avoid trivial fixes:** Please don't open PRs for isolated typos, minor formatting, or other trivial changes. Instead, batch these with a meaningful PR or discussion.
- **Describe your reasoning:** Clearly explain *why* you're opening this PR in the pull request description. Include:
  - What problem does this solve or what improvement does it provide?
  - Why is this change necessary?
  - Any relevant context, links to issues, or design decisions.

### Before You Start

- **Review the documentation:** Check the project documentation (in the `docs/` folder) to ensure your change isn't already covered or documented. This helps you understand existing patterns and functionality.
- **Avoid duplicating functionality:** Look for existing modules and utilities that might be extended rather than creating new ones. For example, if adding a feature to the API, check if it belongs in an existing module before creating a new one.
- **Discuss major changes:** If you're unsure whether your approach aligns with the project's architecture or design, open an issue first to discuss it.

### Submission Steps

1. Fork the repository and create your branch from `main`.
2. Make your changes, following good code practices and adding tests if appropriate.
3. Ensure your code passes linting and tests locally:
   - Run tests: `uv run --extra dev pytest tests/`
   - Check linting and formatting: `make lint-fix` and `make format-fix`
4. Open a pull request with a clear description of your changes (see above for guidance on reasoning and context).

### Automated Checks

Pre-commit hooks will automatically run linting and formatting checks on your code before committing. This ensures consistency and catches common issues early. The hooks enforce:
- **Linting:** `ruff check` with auto-fix enabled
- **Formatting:** `ruff format` with preview mode enabled

If pre-commit is not yet installed, set it up with: `pre-commit install`

## Style & Docstrings

We use [ruff](https://docs.astral.sh/ruff/) for formatting and linting and enforce **Google-style docstrings** (`D` rules via pydocstyle). Please:

- Keep line length within the configured limit (`line-length` in `pyproject.toml`).
- Write a concise summary line (imperative mood) followed by a blank line for multi-line docstrings.
- Include `Args:`, `Returns:`, `Raises:` where applicable.
- Avoid redundancy—do not restate parameter types if already type-annotated unless clarification helps.
- Use triple double quotes for all docstrings.

Minimal examples:

```python
def add(a: int, b: int) -> int:
	"""Return the sum of two integers."""

def fetch_item(key: str) -> dict:
	"""Fetch an item by key.

	Args:
		key: Cache or datastore lookup key.

	Returns:
		A dictionary representing the stored item.

	Raises:
		KeyError: If the key is not found.
	"""
```

You can auto-fix many issues:

```bash
uv run --extra dev ruff check . --fix
uv run --extra dev ruff format
```

Pre-commit will run these checks automatically (see `.pre-commit-config.yaml`).

## Code of Conduct

Please be respectful and inclusive. Disrespectful or inappropriate behavior will not be tolerated.
