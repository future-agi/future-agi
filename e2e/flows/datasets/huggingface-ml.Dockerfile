FROM futureagi/future-agi:e2e-local
# Install the locked ml extra into both API and worker images. Hub transport is
# real; only the external Hub server is replaced by the isolated HTTP fixture.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY futureagi/pyproject.toml futureagi/uv.lock /tmp/hf-deps/
RUN cd /tmp/hf-deps && uv export --frozen --no-dev --extra ml --no-emit-project --no-hashes -o requirements.txt \
    && uv pip install --system -r requirements.txt \
    && python -c "import datasets; assert datasets.__version__ == '4.5.0'"
RUN uv pip install --system pytest==9.0.2 pytest-django==4.11.1
COPY futureagi/conftest.py futureagi/pytest.ini /app/backend/
COPY futureagi/model_hub/tests/conftest.py \
     futureagi/model_hub/tests/test_huggingface_revision.py \
     futureagi/model_hub/tests/test_dataset_huggingface_api.py \
     futureagi/model_hub/tests/test_dataset_creation_import_api.py \
     futureagi/model_hub/tests/test_dataset_runtime_contracts.py \
     /app/backend/model_hub/tests/
COPY futureagi/tfc/temporal/background_tasks/tests/conftest.py \
     futureagi/tfc/temporal/background_tasks/tests/test_activities.py \
     /app/backend/tfc/temporal/background_tasks/tests/
COPY e2e/flows/datasets/huggingface-standin.py /opt/hf-standin/server.py
