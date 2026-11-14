import os

# FI Server Base URL (same as Django settings.BASE_URL)
API_BASE_URL = os.getenv("BASE_URL", "https://api.futureagi.com")
LOG_INFERENCE_URL = f"{API_BASE_URL}/api/v1/log/inference"
