# Makefile for agentic_eval testing

.PHONY: help test test-unit test-integration test-performance test-coverage test-html test-parallel test-fast install-test clean lint format

# Default target
help:
	@echo "🧪 Agentic Eval Test Commands"
	@echo "=============================="
	@echo ""
	@echo "Test Commands:"
	@echo "  make test              - Run all tests"
	@echo "  make test-unit         - Run unit tests only"
	@echo "  make test-integration  - Run integration tests only"
	@echo "  make test-performance  - Run performance tests only"
	@echo "  make test-coverage     - Run tests with coverage"
	@echo "  make test-html         - Generate HTML test report"
	@echo "  make test-parallel     - Run tests in parallel"
	@echo "  make test-fast         - Run tests quickly (skip slow tests)"
	@echo ""
	@echo "Setup Commands:"
	@echo "  make install-test      - Install test dependencies"
	@echo "  make clean             - Clean test artifacts"
	@echo ""
	@echo "Quality Commands:"
	@echo "  make lint              - Run linting"
	@echo "  make format            - Format code"

# Test commands
test:
	@echo "🧪 Running all tests..."
	python3 -m pytest tests/ -v

test-unit:
	@echo "🔍 Running unit tests..."
	python3 run_tests.py --unit --verbose

test-integration:
	@echo "🔗 Running integration tests..."
	python3 run_tests.py --integration --verbose

test-performance:
	@echo "⚡ Running performance tests..."
	python3 run_tests.py --performance --verbose

test-coverage:
	@echo "📊 Running tests with coverage..."
	python3 run_tests.py --coverage --verbose

test-html:
	@echo "📄 Generating HTML test report..."
	python3 run_tests.py --html --verbose

test-parallel:
	@echo "🚀 Running tests in parallel..."
	python3 run_tests.py --parallel --verbose

test-fast:
	@echo "⚡ Running fast tests..."
	python3 run_tests.py --fast --verbose

# Specific test files
test-serving:
	@echo "🌐 Testing serving client..."
	python3 -m pytest tests/test_serving_client.py -v

# Setup commands
install-test:
	@echo "📦 Installing test dependencies..."
	pip install -r requirements-test.txt

# Clean commands
clean:
	@echo "🧹 Cleaning test artifacts..."
	rm -rf htmlcov/
	rm -rf reports/
	rm -rf .pytest_cache/
	rm -rf .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Quality commands
lint:
	@echo "🔍 Running linting..."
	flake8 core/ tests/ --max-line-length=100
	mypy core/ --ignore-missing-imports

format:
	@echo "✨ Formatting code..."
	black core/ tests/ --line-length=100
	isort core/ tests/ --profile black

# Development commands
install-dev: install-test
	@echo "🛠️ Installing development dependencies..."
	pip install -e .

# Continuous integration
ci: lint test-coverage
	@echo "🎯 CI pipeline complete"

# Quick test for development
quick:
	@echo "⚡ Quick test run..."
	python3 -m pytest tests/test_serving_client.py::TestModelServingClient::test_embed_text_string_input -v 