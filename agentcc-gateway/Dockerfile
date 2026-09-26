# Future AGI agentcc-gateway (futureagi/agentcc-gateway): the LLM gateway, one
# static binary on an empty (scratch) base.
# Conventions shared by every futureagi/* image: docs/images.md.
#
# The toolchain tag floats on the 1.26 minor on purpose: Go patch releases
# carry standard-library security fixes, and those are compiled into the
# binary, so each release build picks them up. Build stages only, never
# shipped; the runtime is `scratch`, which has no tag or digest to pin.
ARG GO_IMAGE=golang:1.26-alpine

# Cross-compile on the build host ($BUILDPLATFORM) instead of running the Go
# toolchain under QEMU for linux/arm64: the binary is static, so only CI time
# changes. -trimpath drops host paths from the binary.
FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS builder
ARG TARGETOS
ARG TARGETARCH

WORKDIR /build

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags="-s -w" -o agentcc-gateway ./cmd/agentcc/

# HEALTHCHECK probe: scratch has no shell, curl or wget. Standard library
# only (~0.8 MB gzip); the same source is in fi-collector/Dockerfile.
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

FROM scratch

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /build/agentcc-gateway /app/agentcc-gateway
COPY --from=healthcheck /out/healthcheck /usr/local/bin/healthcheck

EXPOSE 8080

# Unprivileged, numeric so Kubernetes runAsNonRoot can verify it: the same
# uid as distroless `nonroot` (fi-collector). The gateway only reads its
# config (/app/config.yaml) and credentials: mount them readable by 65532
# (0644, or a Kubernetes Secret's default mode). File sinks it can be
# configured with (a `disk` cache backend, a file audit log) need a mounted
# directory that uid can write.
USER 65532:65532
STOPSIGNAL SIGTERM
# /healthz on the listener (config.yaml server.port, 8080 by default). Set
# HEALTHCHECK_URL when the gateway listens elsewhere.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["/usr/local/bin/healthcheck", "http://127.0.0.1:8080/healthz"]
ENTRYPOINT ["/app/agentcc-gateway"]
CMD ["--config", "/app/config.yaml"]

# Image metadata (docs/images.md). Declared last: a new version, commit or
# build time changes the image config only, never a cached layer.
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=""
LABEL org.opencontainers.image.title="Future AGI agentcc-gateway" \
      org.opencontainers.image.description="Future AGI LLM gateway (Agent Command Center): one OpenAI-compatible endpoint in front of every model provider, on :8080." \
      org.opencontainers.image.source="https://github.com/future-agi/future-agi" \
      org.opencontainers.image.url="https://futureagi.com" \
      org.opencontainers.image.documentation="https://github.com/future-agi/future-agi/blob/main/docs/images.md" \
      org.opencontainers.image.vendor="Future AGI" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"
