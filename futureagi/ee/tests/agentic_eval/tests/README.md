# Agentic Eval Tests

This directory contains test cases for the `agentic_eval` package components.

## Test Files

- `test_serving_client.py` - Comprehensive tests for ModelServingClient

## Running Tests

### Run All Tests
```bash
# From the root directory
python -m pytest agentic_eval/tests/ -v

# Run with coverage
python -m pytest agentic_eval/tests/ -v --cov=agentic_eval
```

### Run Specific Test File
```bash
# ModelServingClient tests
python -m pytest agentic_eval/tests/test_serving_client.py -v

# Run specific test class
python -m pytest agentic_eval/tests/test_serving_client.py::TestModelServingClient -v

# Run specific test method
python -m pytest agentic_eval/tests/test_serving_client.py::TestModelServingClient::test_embed_text_string_input -v
```

### Run with Performance Monitoring
```bash
# Show slowest tests
python -m pytest agentic_eval/tests/ -v --durations=10

# Stop on first failure
python -m pytest agentic_eval/tests/ -v -x

# Run with detailed output
python -m pytest agentic_eval/tests/ -v -s
```

### Test Coverage
```bash
# Generate coverage report
python -m pytest agentic_eval/tests/ --cov=agentic_eval --cov-report=html

# View coverage in terminal
python -m pytest agentic_eval/tests/ --cov=agentic_eval --cov-report=term-missing
```

## Test Structure

### ModelServingClient Tests (`test_serving_client.py`)

- **TestModelServingClient**: Core functionality tests
  - Initialization and configuration
  - Request handling and error scenarios
  - All embedding methods (text, image, audio, image-text)
  - Input processing and validation

- **TestGlobalClientFunctions**: Global client management
  - Singleton pattern testing
  - Resource cleanup

- **TestErrorScenarios**: Error handling
  - Network failures
  - Invalid responses
  - Timeout scenarios

- **TestPerformance**: Basic performance tests
  - Batch processing
  - Large input handling

## Adding New Tests

When adding new test files:

1. Follow the naming convention: `test_<component_name>.py`
2. Import the component being tested from the appropriate module
3. Use descriptive test method names: `test_<functionality>_<scenario>`
4. Include setup/teardown methods for proper test isolation
5. Mock external dependencies (HTTP calls, file operations, etc.)

## Dependencies

Make sure you have the test dependencies installed:

```bash
pip install pytest pytest-cov pillow requests
```

## Continuous Integration

These tests are designed to run in CI environments without external dependencies by using mocks for:
- HTTP requests
- File system operations
- External services

All tests should pass consistently and not depend on external network access. 