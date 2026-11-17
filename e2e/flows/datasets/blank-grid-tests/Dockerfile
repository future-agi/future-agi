# Test dependencies are isolated from the application image.
FROM futureagi/future-agi:e2e-local
RUN pip install --no-cache-dir pytest==8.4.1 pytest-django==4.11.1 pytest-asyncio==1.2.0 pytest-mock==3.14.1
COPY futureagi/conftest.py futureagi/pytest.ini /app/backend/
COPY futureagi/model_hub/tests/test_blank_grid.py /app/backend/model_hub/tests/test_blank_grid.py
