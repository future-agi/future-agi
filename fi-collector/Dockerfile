# Multi-stage build — distroless runtime with no shell or package manager.
# The build stage uses the official golang image so
# we get the toolchain + git for module fetch; the runtime stage strips
# everything except the three static commands and collector config.

FROM golang:1.24-alpine AS build
WORKDIR /src

# Cache module downloads in a separate layer so source edits don't
# re-trigger `go mod download`.
COPY go.mod ./
COPY go.sum* ./
RUN go mod download

COPY . .

# Static build (CGO_ENABLED=0) so we can drop to distroless static.
# -trimpath + -ldflags="-s -w" strips debug info → ~30% smaller binary.
RUN CGO_ENABLED=0 GOOS=linux go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-collector ./cmd/fi-collector && \
    CGO_ENABLED=0 GOOS=linux go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-property-catalog-consumer ./cmd/fi-property-catalog-consumer && \
    CGO_ENABLED=0 GOOS=linux go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-observed-catalog-backfill ./cmd/fi-observed-catalog-backfill && \
    mkdir -p /out/data

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=build /out/fi-collector /usr/local/bin/fi-collector
COPY --from=build /out/fi-property-catalog-consumer /usr/local/bin/fi-property-catalog-consumer
COPY --from=build /out/fi-observed-catalog-backfill /usr/local/bin/fi-observed-catalog-backfill
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
