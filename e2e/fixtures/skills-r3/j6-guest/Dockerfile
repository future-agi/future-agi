# J6 VM container guest (synthetic fixture). Not an E2B/Simulation template.
# Build context: pinned SDK and application wheels in wheels/, plus the sidecar.
FROM python:3.11-slim
COPY wheels/ /wheels/
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels \
 && useradd --uid 10001 --create-home guest
COPY relay.py /opt/relay/relay.py
USER 10001
WORKDIR /home/guest
