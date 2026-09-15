# Multi-stage build — distroless for a < 20 MB final image, no shell,
# no package manager. The build stage uses the fi-collector-base image
# (golang + `go mod download` already baked in, see Dockerfile.base) so
# source edits don't re-trigger module downloads; the runtime stage strips
# everything except the static binary.

FROM futureagi/fi-collector-base:v1.0.0 AS build

COPY . .

# Static build (CGO_ENABLED=0) so we can drop to distroless static.
# -trimpath + -ldflags="-s -w" strips debug info → ~30% smaller binary.
RUN CGO_ENABLED=0 GOOS=linux go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-collector ./cmd/fi-collector && \
    CGO_ENABLED=0 GOOS=linux go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-catalog-consumer ./cmd/fi-catalog-consumer && \
    CGO_ENABLED=0 GOOS=linux go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-property-catalog-consumer ./cmd/fi-property-catalog-consumer && \
    CGO_ENABLED=0 GOOS=linux go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-property-catalog-sequencer ./cmd/fi-property-catalog-sequencer && \
    mkdir -p /out/data

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=build /out/fi-collector /usr/local/bin/fi-collector
COPY --from=build /out/fi-catalog-consumer /usr/local/bin/fi-catalog-consumer
COPY --from=build /out/fi-property-catalog-consumer /usr/local/bin/fi-property-catalog-consumer
COPY --from=build /out/fi-property-catalog-sequencer /usr/local/bin/fi-property-catalog-sequencer
COPY config/collector.yaml /etc/fi-collector/config.yaml
COPY --from=build --chown=65532:65532 /out/data /var/lib/fi-collector

# OTLP/gRPC + OTLP/HTTP
EXPOSE 4317 4318

# Dead-letter destination — bind-mount in compose for durability across
# container restarts.
VOLUME ["/var/lib/fi-collector"]

USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/fi-collector"]
CMD ["-config", "/etc/fi-collector/config.yaml"]
