#!/usr/bin/env python3
"""
Simple linting script for ZorkGPT project.

Usage:
    python lint.py                 # Run all linting checks
    python lint.py --fix           # Auto-fix formatting issues
    python lint.py --strict        # Run with strict mode
    python lint.py ruff            # Run only ruff
    python lint.py mypy            # Run only mypy
    python lint.py black           # Run only black
    python lint.py test            # Run tests
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], description: str, cwd: Path | None = None) -> bool:
    """Run a command and return success status."""
    print(f"\n{'='*50}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 50)

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or Path.cwd(),
            capture_output=False,  # Show output in real-time
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        print(f"❌ {description} not found. Install with: pip install {cmd[0]}")
        return False
    except Exception as e:
        print(f"❌ Error running {description}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Lint and format ZorkGPT codebase")
    parser.add_argument("--fix", action="store_true", help="Auto-fix formatting issues")
    parser.add_argument("--strict", action="store_true", help="Run with strict mode")
    parser.add_argument("tools", nargs="*", help="Specific tools to run (default: all)")

    args = parser.parse_args()

    project_root = Path(__file__).parent
    src_dirs = ["."]  # Check entire project

    # Define available tools
    tools = {
        "ruff": {
            "check": ["ruff", "check"] + src_dirs,
            "fix": ["ruff", "check", "--fix"] + src_dirs,
            "description": "Ruff (fast linter and formatter)",
        },
        "mypy": {
            "check": ["mypy"] + src_dirs,
            "fix": None,  # MyPy doesn't auto-fix
            "description": "MyPy (type checker)",
        },
        "black": {
            "check": ["black", "--check", "--diff"] + src_dirs,
            "fix": ["black"] + src_dirs,
            "description": "Black (code formatter)",
        },
        "test": {
            "check": ["python", "-m", "pytest", "--tb=short"],
            "fix": None,
            "description": "Pytest (test runner)",
        },
    }

    # Determine which tools to run
    if args.tools:
        selected_tools = [t for t in args.tools if t in tools]
        if not selected_tools:
            print(f"❌ No valid tools specified. Available: {', '.join(tools.keys())}")
            return 1
    else:
        selected_tools = list(tools.keys())

    # Remove test from default if not explicitly requested and we're doing strict mode
    if not args.tools and args.strict:
        selected_tools = [t for t in selected_tools if t != "test"]

    print("🔍 ZorkGPT Code Quality Check")
    print(f"📁 Project: {project_root}")
    print(f"🛠️  Tools: {', '.join(selected_tools)}")
    print(f"🔧 Fix mode: {'ON' if args.fix else 'OFF'}")
    print(f"⚡ Strict mode: {'ON' if args.strict else 'OFF'}")

    results = {}
    failed_tools = []

    for tool_name in selected_tools:
        tool_config = tools[tool_name]

        if args.fix and tool_config["fix"]:
            # Run fix command
            success = run_command(
                tool_config["fix"],
                f"{tool_config['description']} (fix mode)",
                project_root,
            )
            results[f"{tool_name}_fix"] = success
            if not success:
                failed_tools.append(f"{tool_name}_fix")
        else:
            # Run check command
            success = run_command(
                tool_config["check"],
                f"{tool_config['description']} (check mode)",
                project_root,
            )
            results[tool_name] = success
            if not success:
                failed_tools.append(tool_name)

    # Summary
    print(f"\n{'='*50}")
    print("📊 SUMMARY")
    print("=" * 50)

    for tool, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{tool:15} {status}")

    if failed_tools:
        print(f"\n❌ {len(failed_tools)} tool(s) failed:")
        for tool in failed_tools:
            print(f"   • {tool}")
        print("\n💡 Try running with --fix to auto-correct formatting issues")
        return 1
    else:
        print("\n🎉 All checks passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
