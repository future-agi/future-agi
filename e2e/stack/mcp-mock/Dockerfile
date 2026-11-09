# H-MCP: agentcc-mcpmock, the MCP upstream of the E2E mcp gateway profile
# (e2e/lib/profile.ts mcp, e2e/lib/public-network.ts MCP_MOCK). Built from this
# checkout by `bin/e2e build mcpmock` with the repository root as context;
# Dockerfile.dockerignore next to this file sends only the mock's source, the
# module files and the scenarios' common fixtures. Test image only: never
# published, never pulled (the overlay sets pull_policy: never).
#
# The mock uses the standard library only (cmd/agentcc-mcpmock/main.go), so the
# build needs no module download. GOTOOLCHAIN=local builds with the image's Go,
# which go.mod's `go 1.25.0` accepts; the toolchain line only names the release
# toolchain, which this test binary does not need.
ARG GO_IMAGE=golang:1.26-alpine

FROM ${GO_IMAGE} AS build
WORKDIR /src
COPY agentcc-gateway/go.mod agentcc-gateway/go.sum ./
COPY agentcc-gateway/cmd/agentcc-mcpmock ./cmd/agentcc-mcpmock
COPY e2e/stack/mcp-mock/main.go ./cmd/e2e-mcpmock/main.go
RUN CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-mod=readonly \
    go build -trimpath -ldflags="-s -w" -o /out/agentcc-mcpmock ./cmd/e2e-mcpmock
# Ephemeral test-only TLS material, created in this image, never sourced from
# the operator. bin/e2e extracts only ca.pem so the gateway can verify TLS.
RUN apk add --no-cache openssl && mkdir -p /tls \
    && openssl req -x509 -newkey rsa:2048 -nodes -days 7 \
      -keyout /tls/key.pem -out /tls/ca.pem -subj '/CN=mcp.e2e-public.test' \
      -addext 'subjectAltName=DNS:mcp.e2e-public.test' \
      -addext 'basicConstraints=critical,CA:TRUE' \
    && chown -R 65532:65532 /tls && chmod 0400 /tls/key.pem

# A static binary and its fixtures, nothing else: no shell, no package manager.
FROM scratch
COPY --from=build /out/agentcc-mcpmock /agentcc-mcpmock
COPY --from=build /tls /tls
COPY agentcc-gateway/scripts/scenarios/fixtures/mcp/common/tools.json \
     agentcc-gateway/scripts/scenarios/fixtures/mcp/common/resources.json \
     agentcc-gateway/scripts/scenarios/fixtures/mcp/common/prompts.json /fixtures/
# The server's next release, served on /mcp-next (main.go).
COPY e2e/stack/mcp-mock/fixtures-next/tools.json \
     e2e/stack/mcp-mock/fixtures-next/resources.json \
     e2e/stack/mcp-mock/fixtures-next/prompts.json /fixtures-next/
USER 65532:65532
ENTRYPOINT ["/agentcc-mcpmock"]
