# Native-verification container runtime for the E2E overlay (synthetic fixture class).
# Built on the VM with the exact SDK and application wheels as build context.
FROM python:3.11-slim-bookworm
ARG CTXHUB_APP_COMMIT
ENV CTXHUB_APP_COMMIT=${CTXHUB_APP_COMMIT} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY wheels/ /tmp/wheels/
COPY wheels.sha256 /tmp/wheels/
RUN cd /tmp/wheels && sha256sum -c wheels.sha256 && \
    pip install --no-cache-dir './futureagi-0.8.0-py3-none-any.whl[otel]' ./fi_instrumentation_otel-1.3.2-py3-none-any.whl 'httpx>=0.27,<1' && rm -rf /tmp/wheels
COPY measure.py runtime.py app_cell.py retained_ci.py production_request.py ci_dataset.json /opt/ctxhub/
WORKDIR /opt/ctxhub
USER 10001:10001
ENTRYPOINT ["python", "/opt/ctxhub/runtime.py"]
