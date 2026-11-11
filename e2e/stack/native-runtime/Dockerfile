# Native-verification container runtime for the E2E overlay (synthetic fixture class).
# Built on the VM with the exact SDK and application wheels as build context.
FROM python:3.11-slim-bookworm
ARG CTXHUB_APP_COMMIT
ENV CTXHUB_APP_COMMIT=${CTXHUB_APP_COMMIT} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY wheels/ /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/futureagi-*.whl 'httpx>=0.27,<1' && rm -rf /tmp/wheels
COPY measure.py runtime.py /opt/ctxhub/
WORKDIR /opt/ctxhub
USER 10001:10001
ENTRYPOINT ["python", "/opt/ctxhub/runtime.py"]
