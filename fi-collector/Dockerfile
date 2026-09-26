# Multi-stage build — distroless runtime with no shell or package manager.
# The build stage uses the official golang image so
# we get the toolchain + git for module fetch; the runtime stage strips
# everything except the three static commands and collector config.
# Conventions shared by every futureagi/* image: docs/images.md.

# The toolchain tag floats on the 1.24 minor on purpose: Go patch releases
# carry standard-library security fixes that are compiled into the binaries.
# Build stages only, never shipped.
ARG GO_IMAGE=golang:1.24-alpine
# distroless static (no libc, no shell), user nonroot (65532). Pinned by
# digest (multi-arch index); base-digest-check.yml reports when it moves.
ARG RUNTIME_IMAGE=gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

# Cross-compile on the build host ($BUILDPLATFORM): the Go toolchain never runs
# under QEMU for linux/arm64. The binaries are static, so this changes CI time
# only.
FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src

# Cache module downloads in a separate layer so source edits don't
# re-trigger `go mod download`.
COPY go.mod ./
COPY go.sum* ./
RUN go mod download

COPY . .

# Static build (CGO_ENABLED=0) so we can drop to distroless static.
# -trimpath + -ldflags="-s -w" strips debug info → ~30% smaller binary.
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-collector ./cmd/fi-collector && \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-property-catalog-consumer ./cmd/fi-property-catalog-consumer && \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build \
    -trimpath -ldflags="-s -w" \
    -o /out/fi-observed-catalog-backfill ./cmd/fi-observed-catalog-backfill && \
    mkdir -p /out/data

# HEALTHCHECK probe: the runtime has no shell, curl or wget. Standard library
# only (~0.8 MB gzip); the same source is in agentcc-gateway/Dockerfile.
FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS healthcheck
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src/healthcheck
RUN printf '%s\n' \
        '// healthcheck: HEALTHCHECK probe for images with no shell (docs/images.md).' \
        '// usage: healthcheck [-pid1 NAME] http://HOST:PORT/PATH' \
        '// Exits 0 when the URL answers HTTP 2xx, else 1. HEALTHCHECK_URL replaces' \
        '// the URL. -pid1 NAME: exit 0 without probing unless PID 1 runs NAME, so a' \
        '// container started with another command of the image is not unhealthy.' \
        'package main' \
        '' \
        'import (' \
        '    "bufio"' \
        '    "net"' \
        '    "os"' \
        '    "strings"' \
        '    "time"' \
        ')' \
        '' \
        'func main() {' \
        '    args := os.Args[1:]' \
        '    if len(args) >= 2 && args[0] == "-pid1" {' \
        '        cmdline, err := os.ReadFile("/proc/1/cmdline")' \
        '        if err == nil && !runs(string(cmdline), args[1]) {' \
        '            return' \
        '        }' \
        '        args = args[2:]' \
        '    }' \
        '    target := os.Getenv("HEALTHCHECK_URL")' \
        '    if target == "" && len(args) == 1 {' \
        '        target = args[0]' \
        '    }' \
        '    rest, ok := strings.CutPrefix(target, "http://")' \
        '    if !ok || rest == "" {' \
        '        fail("usage: healthcheck [-pid1 NAME] http://HOST:PORT/PATH")' \
        '    }' \
        '    host, path, _ := strings.Cut(rest, "/")' \
        '    if _, _, err := net.SplitHostPort(host); err != nil {' \
        '        host = net.JoinHostPort(host, "80")' \
        '    }' \
        '    conn, err := net.DialTimeout("tcp", host, 3*time.Second)' \
        '    if err != nil {' \
        '        fail(err.Error())' \
        '    }' \
        '    defer conn.Close()' \
        '    _ = conn.SetDeadline(time.Now().Add(5 * time.Second))' \
        '    request := "GET /" + path + " HTTP/1.0\r\nHost: " + host + "\r\nUser-Agent: healthcheck\r\n\r\n"' \
        '    if _, err := conn.Write([]byte(request)); err != nil {' \
        '        fail(err.Error())' \
        '    }' \
        '    status, err := bufio.NewReader(conn).ReadString(10)' \
        '    if err != nil {' \
        '        fail(err.Error())' \
        '    }' \
        '    if fields := strings.Fields(status); len(fields) < 2 || !strings.HasPrefix(fields[1], "2") {' \
        '        fail(strings.TrimSpace(status))' \
        '    }' \
        '}' \
        '' \
        '// runs: does the NUL-separated command line hold NAME (argv0 or after --)?' \
        'func runs(cmdline, name string) bool {' \
        '    for _, arg := range strings.Split(cmdline, "\x00") {' \
        '        if arg == name || strings.HasSuffix(arg, "/"+name) {' \
        '            return true' \
        '        }' \
        '    }' \
        '    return false' \
        '}' \
        '' \
        'func fail(message string) {' \
        '    os.Stderr.WriteString("healthcheck: " + message + "\n")' \
        '    os.Exit(1)' \
        '}' \
        > main.go \
    && CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
        go build -trimpath -ldflags="-s -w" -o /out/healthcheck main.go

FROM ${RUNTIME_IMAGE}
WORKDIR /
COPY --from=build /out/fi-collector /usr/local/bin/fi-collector
COPY --from=build /out/fi-property-catalog-consumer /usr/local/bin/fi-property-catalog-consumer
COPY --from=build /out/fi-observed-catalog-backfill /usr/local/bin/fi-observed-catalog-backfill
COPY config/collector.yaml /etc/fi-collector/config.yaml
COPY --from=build --chown=65532:65532 /out/data /var/lib/fi-collector
COPY --from=healthcheck /out/healthcheck /usr/local/bin/healthcheck

# OTLP/gRPC + OTLP/HTTP
EXPOSE 4317 4318

# Dead-letter destination — bind-mount in compose for durability across
# container restarts.
VOLUME ["/var/lib/fi-collector"]

USER nonroot:nonroot
STOPSIGNAL SIGTERM
# /healthz on the admin port (:9464, cmd/fi-collector/main.go). The same image
# runs fi-property-catalog-consumer and fi-observed-catalog-backfill, which
# serve no admin port: -pid1 skips the probe for them. Set HEALTHCHECK_URL
# when the admin server listens elsewhere.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["/usr/local/bin/healthcheck", "-pid1", "fi-collector", "http://127.0.0.1:9464/healthz"]
ENTRYPOINT ["/usr/local/bin/fi-collector"]
CMD ["-config", "/etc/fi-collector/config.yaml"]

# Image metadata (docs/images.md). Declared last: a new version, commit or
# build time changes the image config only, never a cached layer.
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=""
LABEL org.opencontainers.image.title="Future AGI fi-collector" \
      org.opencontainers.image.description="OTLP (gRPC :4317, HTTP :4318) trace receiver that writes spans to ClickHouse; also ships the property-catalog consumer and observed-catalog backfill commands." \
      org.opencontainers.image.source="https://github.com/future-agi/future-agi" \
      org.opencontainers.image.url="https://futureagi.com" \
      org.opencontainers.image.documentation="https://github.com/future-agi/future-agi/blob/main/docs/images.md" \
      org.opencontainers.image.vendor="Future AGI" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"
