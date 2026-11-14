# The E2E fixture code exists only in this locally built image. The normal
# backend image and its capability checks remain unchanged.
ARG BASE_TAG=e2e-local
FROM futureagi/future-agi:${BASE_TAG}

COPY e2e/stack/backend/e2e_clean_signup.py /app/backend/accounts/views/e2e_clean_signup.py
COPY e2e/stack/backend/settings_e2e.py /app/backend/tfc/settings/e2e.py
COPY e2e/stack/backend/urls_e2e.py /app/backend/tfc/urls_e2e.py
COPY e2e/stack/backend/e2e_feed_issue.py /app/backend/tracer/views/e2e_feed_issue.py

COPY e2e/stack/backend/bootstrap_sql.py /app/backend/bootstrap_sql.py

# Attachments (OBS-E2E-066). The validate drain runs the real bubblewrap
# sandbox, which needs /usr/bin/bwrap and /usr/bin/python3; the product image
# ships neither (tracer/services/attachments/DEPLOY.md: the tasks_s worker image
# needs bwrap). The worker's container options are in docker-compose.e2e.yml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap python3 \
    && rm -rf /var/lib/apt/lists/* \
    && test -x /usr/bin/bwrap && test -x /usr/bin/prlimit && test -x /usr/bin/python3
COPY e2e/stack/attachments/aws-config /etc/futureagi-e2e/aws-config
COPY e2e/stack/backend/e2e_attachment_scope.py /app/backend/tracer/views/e2e_attachment_scope.py
COPY e2e/stack/backend/bootstrap_attachments_storage.py /app/backend/bootstrap_attachments_storage.py
