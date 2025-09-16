# Linting and Code Quality

This project includes a comprehensive linting setup to maintain code quality and consistency.

## Quick Start

```bash
# Install linting tools
pip install -e ".[dev]"  # or uv sync --group dev

# Run all linting checks
python lint.py

# Auto-fix formatting issues
python lint.py --fix

# Run specific tools only
python lint.py ruff mypy

# Run in strict mode (excludes tests)
python lint.py --strict
```

## Available Tools

### Ruff (Fast Linter & Formatter)
- **Purpose**: Fast Python linter and code formatter
- **Checks**: Style, imports, complexity, bugs
- **Auto-fix**: Yes, for most issues
- **Config**: `[tool.ruff]` in `pyproject.toml`

### MyPy (Type Checker)
- **Purpose**: Static type checking
- **Checks**: Type annotations, type safety
- **Auto-fix**: No
- **Config**: `[tool.mypy]` in `pyproject.toml`

### Black (Code Formatter)
- **Purpose**: Opinionated code formatter
- **Checks**: Code formatting consistency
- **Auto-fix**: Yes
- **Config**: `[tool.black]` in `pyproject.toml`

### Pytest (Test Runner)
- **Purpose**: Run test suite
- **Checks**: Test failures
- **Auto-fix**: No
- **Config**: `[tool.pytest.ini_options]` in `pyproject.toml`

## Usage Examples

```bash
# Check everything
python lint.py

# Fix formatting issues
python lint.py --fix

# Only run ruff and mypy
python lint.py ruff mypy

# Quick check without tests
python lint.py --strict

# Format code with black only
python lint.py black --fix
```

## Configuration

All tools are configured in `pyproject.toml`:

- **Ruff**: Comprehensive linting rules with sensible defaults
- **MyPy**: Permissive type checking (can be made stricter over time)
- **Black**: Standard 88-character line length
- **Pytest**: Standard test discovery and reporting

## Development Workflow

1. **Before committing**: Run `python lint.py --fix`
2. **CI/CD**: Run `python lint.py --strict`
3. **Pre-commit**: Consider adding to your git hooks

## Troubleshooting

- **Tool not found**: Install with `pip install -e ".[dev]"`
- **Permission denied**: Make script executable with `chmod +x lint.py`
- **Configuration issues**: Check `pyproject.toml` for tool-specific settings

## IDE Integration

Most modern IDEs can use these tools:

- **VS Code**: Install Python extensions, they'll auto-detect the configs
- **PyCharm**: Configure external tools or use built-in support
- **Vim/Neovim**: Use ALE or similar plugins

## Adding New Rules

To customize linting rules:

1. Edit the respective `[tool.*]` section in `pyproject.toml`
2. Test with `python lint.py <tool_name>`
3. Update documentation if needed
