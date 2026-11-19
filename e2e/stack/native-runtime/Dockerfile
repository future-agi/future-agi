# Native-verification container runtime for the E2E overlay (synthetic fixture class).
# Built on the VM with the exact SDK and application wheels as build context.
FROM python:3.11-slim-bookworm
ARG CTXHUB_APP_COMMIT
ENV CTXHUB_APP_COMMIT=${CTXHUB_APP_COMMIT} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY wheels/ /tmp/wheels/
RUN cd /tmp/wheels && sha256sum -c SHA256SUMS && \
    set -- ./futureagi-*.whl && pip install --no-cache-dir "${1}[otel]" ./fi_instrumentation_otel-*.whl 'httpx>=0.27,<1' && rm -rf /tmp/wheels
COPY measure.py runtime.py app_cell.py retained_ci.py production_request.py ci_dataset.json /opt/ctxhub/
WORKDIR /opt/ctxhub
USER 10001:10001
ENTRYPOINT ["python", "/opt/ctxhub/runtime.py"]
