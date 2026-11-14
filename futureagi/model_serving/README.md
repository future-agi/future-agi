# Model Serving API 🚀

High-performance embedding model serving service built with FastAPI, PyTorch, and UV package management.

## ✨ Features

- **🚀 Fast API**: Built with FastAPI for high-performance API serving
- **🧠 Multiple Models**: Support for text, image, audio, and multimodal embeddings
- **📦 UV Package Management**: Lightning-fast dependency resolution and installation
- **🐳 Docker Ready**: Multi-stage Docker builds with optimized layer caching
- **⚡ Performance**: Lazy loading, connection pooling, and concurrent processing
- **🔒 Security**: Non-root containers, input validation, and comprehensive error handling
- **📊 Monitoring**: Health checks, logging, and metrics integration
- **🛠 Developer Friendly**: CLI tools, hot reloading, and comprehensive testing

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- UV package manager
- Docker (optional)

### Installation with UV

```bash
# Install UV if you haven't already
pip install uv

# Clone the repository
git clone <repository-url>
cd model_serving

# Install dependencies with UV (much faster than pip!)
uv sync

# Run the development server
uv run serve --reload
```

### Alternative Installation Methods

```bash
# Install with optional dependencies
uv sync --extra dev          # Development tools
uv sync --extra gpu          # GPU support
uv sync --extra monitoring   # Monitoring tools

# Or install all extras
uv sync --all-extras
```

## 🏃 Running the Service

### Development

```bash
# Start development server with auto-reload
uv run serve --reload --log-level debug

# Or use the CLI directly
uv run model-serving serve --host 0.0.0.0 --port 8080 --reload
```

### Production

```bash
# Start production server with multiple workers
uv run serve --workers 4 --log-level info

# Or use gunicorn directly
uv run gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080
```

### Docker

```bash
# Build the image
docker build -t model-serving .

# Run the container
docker run -p 8080:8080 model-serving

# Run with environment variables
docker run -p 8080:8080 -e CORS_ORIGINS="*" -e LOG_LEVEL="debug" model-serving
```

## 🔧 CLI Commands

The project includes a comprehensive CLI for managing the service:

```bash
# Show help
uv run model-serving --help

# Start the server
uv run model-serving serve --host 0.0.0.0 --port 8080

# Check health
uv run model-serving health --host localhost --port 8080

# List available models
uv run model-serving models --format table

# Show system information
uv run model-serving info
```

## 📡 API Endpoints

### Core Endpoints

- `GET /` - Root endpoint with service info
- `GET /health` - Health check endpoint
- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - Alternative API documentation

### Model Serving Endpoints

- `POST /model/v1/embed` - Text embeddings
- `POST /model/v1/embed/image` - Image embeddings
- `POST /model/v1/embed/audio` - Audio embeddings
- `POST /model/v1/embed/image-text` - Multimodal embeddings
- `GET /model/v1/models` - List available models

### 🧪 Bruno API Collection

For comprehensive API testing and documentation, we provide a **Bruno collection** with ready-to-use requests:

📁 **Location**: `bruno/` directory
🚀 **Features**:
- Pre-configured environments (Development, Docker, Production)
- Comprehensive test cases with validation
- Error scenario testing
- Performance benchmarks
- Detailed documentation for each endpoint

**Quick Start with Bruno:**
1. Install [Bruno](https://www.usebruno.com/) API client
2. Open the `bruno/` collection in Bruno
3. Select your environment (Development/Docker/Production)
4. Start testing endpoints with one click!

See `bruno/README.md` for detailed usage instructions.

### Example API Usage

```bash
# Text embeddings
curl -X POST "http://localhost:8080/model/v1/embed" \
     -H "Content-Type: application/json" \
     -d '{"text": ["Hello world"], "input_type": "text"}'

# Health check
curl http://localhost:8080/health
```

## 🛠 Development

### Setting up Development Environment

```bash
# Install development dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=app --cov-report=html

# Format code
uv run black app tests
uv run isort app tests

# Lint code
uv run flake8 app tests

# Type checking
uv run mypy app
```

### Project Structure

```
model_serving/
├── pyproject.toml          # UV project configuration
├── uv.lock                 # Dependency lock file (auto-generated)
├── Dockerfile              # Multi-stage Docker build
├── entrypoint.sh           # Docker entrypoint script
├── README.md               # This file
├── app/                    # Main application code
│   ├── __init__.py        # Package initialization with version
│   ├── main.py            # FastAPI application
│   ├── cli.py             # Command-line interface
│   ├── v1/                # API v1 routes
│   ├── config/            # Configuration modules
│   ├── servable_models/   # Model implementations
│   └── utils/             # Utility functions
└── tests/                 # Test files
```

## ⚡ Performance Features

### UV Benefits

- **🚀 10-100x faster** dependency resolution compared to pip
- **📦 Better caching** for faster rebuilds
- **🔒 Reproducible builds** with lock files
- **🎯 Exact version resolution** preventing dependency conflicts

### Model Serving Optimizations

- **Lazy Loading**: Models load on-demand to reduce startup time and memory usage
- **Thread Safety**: Proper locking for concurrent access
- **Connection Pooling**: Reused HTTP connections for better performance
- **Caching**: Health check and model status caching
- **Compression**: GZip middleware for response compression

## 🔧 Configuration

### Environment Variables

```bash
# Server configuration
HOST=0.0.0.0                    # Server host
PORT=8080                       # Server port
WORKERS=4                       # Number of worker processes

# CORS configuration
CORS_ORIGINS=*                  # Allowed CORS origins

# Features
ENABLE_DOCS=true                # Enable API documentation

# Logging
LOG_LEVEL=info                  # Logging level

# Model serving
MODEL_SERVING_URL=http://localhost:8080
MODEL_SERVING_TIMEOUT=30
MODEL_SERVING_MAX_RETRIES=3
```

### Model Configuration

Models are configured in `app/servable_models/constants.py`. You can:

- Enable/disable specific models
- Configure model parameters
- Set custom model paths
- Adjust memory and performance settings

## 🧪 Testing

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=term-missing --cov-report=html

# Run specific test categories
uv run pytest -m unit           # Unit tests only
uv run pytest -m integration    # Integration tests only
uv run pytest -m performance    # Performance tests only

# Run tests in parallel
uv run pytest -n auto
```

### Test Categories

- **Unit Tests**: Fast, isolated component tests
- **Integration Tests**: API endpoint and service integration tests
- **Performance Tests**: Load testing and benchmarking

## 📊 Monitoring

### Health Checks

The service provides comprehensive health checking:

```bash
# Basic health check
curl http://localhost:8080/health

# Detailed model status
curl http://localhost:8080/model/v1/models

# CLI health check with timeout
uv run model-serving health --timeout 10
```

### Logging

Structured logging with configurable levels:

- **DEBUG**: Detailed debugging information
- **INFO**: General operational messages
- **WARNING**: Warning conditions
- **ERROR**: Error conditions
- **CRITICAL**: Critical error conditions

### Metrics (Optional)

Install monitoring extras for advanced metrics:

```bash
uv sync --extra monitoring
```

## 🐳 Docker

### Building

```bash
# Build the image
docker build -t model-serving .

# Build with specific tag
docker build -t model-serving:v1.0.0 .
```

### Running

```bash
# Basic run
docker run -p 8080:8080 model-serving

# With environment variables
docker run -p 8080:8080 \
  -e WORKERS=2 \
  -e LOG_LEVEL=debug \
  -e CORS_ORIGINS="http://localhost:3000" \
  model-serving

# With volume mounts for models
docker run -p 8080:8080 \
  -v /path/to/models:/app/models \
  model-serving
```

### Docker Compose

```yaml
version: '3.8'
services:
  model-serving:
    build: .
    ports:
      - "8080:8080"
    environment:
      - WORKERS=4
      - LOG_LEVEL=info
      - CORS_ORIGINS=*
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Install development dependencies (`uv sync --extra dev`)
4. Make your changes
5. Run tests (`uv run pytest`)
6. Format code (`uv run black . && uv run isort .`)
7. Commit your changes (`git commit -m 'Add amazing feature'`)
8. Push to the branch (`git push origin feature/amazing-feature`)
9. Open a Pull Request

## 📋 Requirements

### System Requirements

- **Python**: 3.8 or higher
- **Memory**: 2GB+ recommended (4GB+ for multiple models)
- **Storage**: 1GB+ for models and dependencies
- **CPU**: Multi-core recommended for production

### GPU Support (Optional)

For GPU acceleration, install with GPU extras:

```bash
uv sync --extra gpu
```

Requirements:
- NVIDIA GPU with CUDA support
- CUDA 11.8 or 12.1+
- cuDNN library

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **FastAPI** for the excellent web framework
- **UV** for revolutionary Python package management
- **PyTorch** for machine learning capabilities
- **Transformers** for state-of-the-art model implementations

---

**Made with ❤️ by the Future AGI Team**
