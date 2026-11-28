# The E2E fixture code exists only in this locally built image. The normal
# backend image and its capability checks remain unchanged.
ARG BASE_TAG=e2e-local
# Both local targets use the same fixture stage. Only the public certificate
# enters the application image. The disposable fixture key stays in the local
# Moto image (never a published/release image). No host cert store is changed.
FROM motoserver/moto:5.1.4 AS attachments-s3
RUN umask 077 && mkdir /fixture-tls && openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout /fixture-tls/server.key -out /fixture-tls/server.crt -days 365 \
    -subj /CN=attachments-s3 -addext subjectAltName=DNS:attachments-s3 2>/dev/null \
    && chmod 644 /fixture-tls/server.crt
COPY e2e/stack/backend/moto_sha256_server.py /fixture/moto_sha256_server.py
ENTRYPOINT ["python", "-I", "/fixture/moto_sha256_server.py", "-H", "0.0.0.0"]
CMD ["-c", "/fixture-tls/server.crt", "-k", "/fixture-tls/server.key"]

FROM futureagi/future-agi:${BASE_TAG}
COPY --from=attachments-s3 /fixture-tls/server.crt /etc/futureagi-e2e/attachments-s3.crt

# Standalone collectstatic also runs Django while assembling this test image.
ENV FUTURE_AGI_TELEMETRY_DISABLED=true FUTURE_AGI_TELEMETRY_URL=http://127.0.0.1:9

COPY e2e/stack/backend/e2e_clean_signup.py /app/backend/accounts/views/e2e_clean_signup.py
COPY e2e/stack/backend/settings_e2e.py /app/backend/tfc/settings/e2e.py
COPY e2e/stack/backend/urls_e2e.py /app/backend/tfc/urls_e2e.py
COPY e2e/stack/backend/e2e_feed_issue.py /app/backend/tracer/views/e2e_feed_issue.py
COPY e2e/stack/backend/e2e_legacy_eval.py /app/backend/sdk/views/e2e_legacy_eval.py

COPY e2e/stack/backend/e2e_google.py /app/backend/tfc/views/e2e_google.py
COPY e2e/stack/backend/bootstrap_sql.py /app/backend/bootstrap_sql.py

# Attachments (OBS-E2E-066). The validate drain runs the real bubblewrap
# sandbox, which needs /usr/bin/bwrap and /usr/bin/python3; the product image
# ships neither (tracer/services/attachments/DEPLOY.md: the tasks_s worker image
# needs bwrap). The worker's container options are in docker-compose.e2e.yml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap python3 \
    && rm -rf /var/lib/apt/lists/* \
    && test -x /usr/bin/bwrap && test -x /usr/bin/prlimit && test -x /usr/bin/python3
# The real peaks worker decodes with the optional audio extra. Install only its
# decoder, locked to the CPython 3.11 Linux wheels in futureagi/uv.lock. Never
# substitute synthetic peak bytes or relax the worker's own seccomp policy.
RUN printf '%s\n' 'av==14.2.0 --hash=sha256:e7647d4a8d1855d05fe70784a962b15e103a2d4a0eba1dea7bfbfd95753dedb9 --hash=sha256:707b3e9ec74d91a163b1b774b592cae32241f9df9b8f6c270ab7c7603e62359d' > /tmp/e2e-decoder.txt \
    && python -m pip install --no-cache-dir --only-binary=:all: --no-deps --require-hashes -r /tmp/e2e-decoder.txt \
    && rm /tmp/e2e-decoder.txt
# Byte storage for OBS-E2E-066. The release build context excludes this module.
COPY e2e/stack/backend/e2e_attachment_storage.py /app/backend/e2e_attachment_storage.py
COPY e2e/stack/backend/e2e_attachment_scope.py /app/backend/tracer/views/e2e_attachment_scope.py
COPY e2e/stack/backend/bootstrap_attachments_storage.py /app/backend/bootstrap_attachments_storage.py
