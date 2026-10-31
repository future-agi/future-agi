# A stand-in for futureagi/standalone in the bin/update Docker end-to-end tests
# (deploy/tests/update_e2e). It answers the API's /health/ on 8000 and the UI on
# 3000, carries the release version as the OCI label bin/update reads for
# installs that follow :latest, and appends one line per start to /data/history
# so the tests can see the data volume survive an update.
#
#   MODE=ok         healthy
#   MODE=unhealthy  the UI answers, /health/ never does
#   MODE=crash      exits 1 at once (a crash loop under restart: unless-stopped)
#   MODE=slow       logs "Applying ..." migration lines for SLOW_SECONDS first
#
# FLAVOR only changes the image content (and so its digest): the tests push a
# second image with the same version to show a moved release tag is refused.
#
# harness.py assembles exactly this image (busybox:1.37's layer, a layer with
# start.sh, this config) straight into the test registry, which is much faster
# than `docker build` + `docker push` on a busy host. Set
# FUTURE_AGI_UPDATE_E2E_BUILD=docker to build it from this file instead.
# There is deliberately no RUN step: start.sh does all setup at start.
FROM busybox:1.37
ARG VERSION=v0.0.0
ARG MODE=ok
ARG SLOW_SECONDS=20
ARG FLAVOR=release
LABEL org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$FLAVOR \
      org.opencontainers.image.title="bin/update e2e fake standalone image"
COPY start.sh /start.sh
ENV VERSION=$VERSION MODE=$MODE SLOW_SECONDS=$SLOW_SECONDS FLAVOR=$FLAVOR
EXPOSE 3000 8000
HEALTHCHECK --interval=2s --timeout=2s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8000/health/ || exit 1
ENTRYPOINT ["/bin/sh", "/start.sh"]
