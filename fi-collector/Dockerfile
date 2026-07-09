# Multi-stage build — distroless for a < 20 MB final image, no shell,
# no package manager. The build stage uses the official golang image so
# we get the toolchain + git for module fetch; the runtime stage strips
# everything except the static binary.

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
    -o /out/fi-collector ./cmd/fi-collector

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /
COPY --from=build /out/fi-collector /usr/local/bin/fi-collector
COPY config/collector.yaml /etc/fi-collector/config.yaml

# OTLP/gRPC + OTLP/HTTP
EXPOSE 4317 4318

# Dead-letter destination — bind-mount in compose for durability across
# container restarts.
VOLUME ["/var/lib/fi-collector"]

USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/fi-collector"]
CMD ["-config", "/etc/fi-collector/config.yaml"]
