# The E2E fixture code exists only in this locally built image. The normal
# backend image and its capability checks remain unchanged.
ARG BASE_TAG=e2e-local
FROM futureagi/future-agi:${BASE_TAG}

COPY e2e/stack/backend/settings_e2e.py /app/backend/tfc/settings/e2e.py
COPY e2e/stack/backend/urls_e2e.py /app/backend/tfc/urls_e2e.py
COPY e2e/stack/backend/e2e_feed_issue.py /app/backend/tracer/views/e2e_feed_issue.py
