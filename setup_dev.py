#!/usr/bin/env python3
"""
Development environment setup script.

Installs all development dependencies and sets up the development environment.
"""

import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], description: str) -> bool:
    """Run a command and return success status."""
    print(f"🔧 {description}")
    print(f"   Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode == 0:
            print(f"   ✅ {description} completed successfully")
            return True
        else:
            print(f"   ❌ {description} failed")
            return False
    except Exception as e:
        print(f"   ❌ Error running {description}: {e}")
        return False


def main():
    """Set up development environment."""
    print("🚀 Setting up ZorkGPT development environment")
    print("=" * 50)

    project_root = Path(__file__).parent

    # Check if we're using uv or pip
    use_uv = False
    try:
        subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True)
        use_uv = True
        print("📦 Using uv package manager")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("📦 Using pip package manager")

    # Install dependencies
    if use_uv:
        success = run_command(
            ["uv", "sync", "--group", "dev"],
            "Installing development dependencies with uv",
        )
    else:
        success = run_command(
            [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
            "Installing development dependencies with pip",
        )

    if not success:
        print("\n❌ Failed to install dependencies")
        return 1

    # Make lint script executable
    lint_script = project_root / "lint.py"
    if lint_script.exists():
        lint_script.chmod(0o755)
        print("✅ Made lint.py executable")

    # Test linting setup
    print("\n🧪 Testing linting setup...")
    test_result = run_command(
        [sys.executable, "lint.py", "--strict"], "Running linting check"
    )

    print("\n" + "=" * 50)
    if test_result:
        print("🎉 Development environment setup complete!")
        print("\n📋 Next steps:")
        print("   • Run 'python lint.py' to check code quality")
        print("   • Run 'python lint.py --fix' to auto-fix issues")
        print("   • Run 'python -m pytest' to run tests")
    else:
        print("⚠️  Setup completed but some linting tools may need attention")
        print("\n💡 Try running 'python lint.py' to see specific issues")

    return 0 if test_result else 1


if __name__ == "__main__":
    sys.exit(main())
