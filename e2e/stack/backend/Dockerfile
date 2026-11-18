# The E2E fixture code exists only in this locally built image. The normal
# backend image and its capability checks remain unchanged.
ARG BASE_TAG=e2e-local
FROM futureagi/future-agi:${BASE_TAG}

# Standalone collectstatic also runs Django while assembling this test image.
ENV FUTURE_AGI_TELEMETRY_DISABLED=true FUTURE_AGI_TELEMETRY_URL=http://127.0.0.1:9

COPY e2e/stack/backend/e2e_clean_signup.py /app/backend/accounts/views/e2e_clean_signup.py
COPY e2e/stack/backend/settings_e2e.py /app/backend/tfc/settings/e2e.py
COPY e2e/stack/backend/urls_e2e.py /app/backend/tfc/urls_e2e.py
COPY e2e/stack/backend/e2e_feed_issue.py /app/backend/tracer/views/e2e_feed_issue.py
COPY e2e/stack/backend/e2e_legacy_eval.py /app/backend/sdk/views/e2e_legacy_eval.py

COPY e2e/stack/backend/e2e_google.py /app/backend/tfc/views/e2e_google.py
COPY e2e/stack/backend/bootstrap_sql.py /app/backend/bootstrap_sql.py
