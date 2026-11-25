#!/usr/bin/env python3
"""
Test runner for agentic_eval

This script provides various test running options for agentic_eval components.

Usage:
    python run_tests.py                    # Run all tests
    python run_tests.py --unit             # Run only unit tests
    python run_tests.py --integration      # Run only integration tests
    python run_tests.py --performance      # Run only performance tests
    python run_tests.py --coverage         # Run with coverage report
    python run_tests.py --html             # Generate HTML test report
    python run_tests.py --parallel         # Run tests in parallel
    python run_tests.py --fast             # Run tests quickly (skip slow tests)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run_command(cmd, description=""):
    """Run a command and handle errors"""
    print(f"\n🔄 {description}")
    print(f"Running: {' '.join(cmd)}")
    print("-" * 60)

    result = subprocess.run(cmd, cwd=Path(__file__).parent)

    if result.returncode == 0:
        print(f"✅ {description} - SUCCESS")
    else:
        print(f"❌ {description} - FAILED")
        sys.exit(result.returncode)

    return result


def main():
    parser = argparse.ArgumentParser(description="Run agentic_eval tests")

    # Test type options
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument(
        "--integration", action="store_true", help="Run integration tests only"
    )
    parser.add_argument(
        "--performance", action="store_true", help="Run performance tests only"
    )

    # Output options
    parser.add_argument(
        "--coverage", action="store_true", help="Run with coverage report"
    )
    parser.add_argument("--html", action="store_true", help="Generate HTML test report")
    parser.add_argument("--parallel", action="store_true", help="Run tests in parallel")
    parser.add_argument("--fast", action="store_true", help="Skip slow tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # Test selection
    parser.add_argument("--file", help="Run specific test file")
    parser.add_argument("--test", help="Run specific test function")
    parser.add_argument("--maxfail", type=int, default=5, help="Stop after N failures")

    args = parser.parse_args()

    # Base pytest command
    cmd = ["python", "-m", "pytest"]

    # Add test path
    if args.file:
        cmd.append(f"tests/{args.file}")
    else:
        cmd.append("tests/")

    # Add specific test
    if args.test:
        cmd.append(f"-k {args.test}")

    # Add markers for test types
    markers = []
    if args.unit:
        markers.append("unit")
    if args.integration:
        markers.append("integration")
    if args.performance:
        markers.append("performance")
    if args.fast:
        markers.append("not slow")

    if markers:
        cmd.extend(["-m", " and ".join(markers)])

    # Add options
    if args.verbose:
        cmd.append("-v")

    cmd.extend(["--maxfail", str(args.maxfail)])

    # Parallel execution
    if args.parallel:
        cmd.extend(["-n", "auto"])

    # Coverage options
    if args.coverage:
        cmd.extend(
            ["--cov=core", "--cov-report=term-missing", "--cov-report=html:htmlcov"]
        )

    # HTML report
    if args.html:
        cmd.extend(["--html=reports/test_report.html", "--self-contained-html"])
        # Create reports directory
        os.makedirs("reports", exist_ok=True)

    # Run the tests
    run_command(cmd, "Running agentic_eval tests")

    # Show coverage report location
    if args.coverage:
        print("\n📊 Coverage report generated:")
        print(f"   HTML: {Path('htmlcov/index.html').absolute()}")

    if args.html:
        print("\n📄 Test report generated:")
        print(f"   HTML: {Path('reports/test_report.html').absolute()}")


if __name__ == "__main__":
    print("🧪 Agentic Eval Test Runner")
    print("=" * 50)

    # Check if we're in the right directory
    if not Path("tests").exists():
        print(
            "❌ Tests directory not found. Please run from agentic_eval root directory."
        )
        sys.exit(1)

    main()
