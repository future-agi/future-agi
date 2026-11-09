# The Omega Hub checker's runner daemon (deploy/README.md, "Omega Hub"): Docker in
# Docker whose only container runtime is gVisor (runsc). The checker
# (services/omega-hub-checker) runs every uploaded entry's keyless test and demo
# through this daemon, never through the application host's own Docker.
#
#   Compose setups: the `hub-runner` service builds and runs it next to the stack.
#   Production:     run it on the dedicated runner host (docker-compose.yml in
#                   this directory), behind an egress firewall, and point the
#                   checker's OMEGA_HUB_RUNNER_DOCKER_HOST at it.
#
# It listens only on TCP 2376 with mutual TLS (the docker:dind entrypoint makes
# a CA, a server and a client certificate under /certs); a client needs the
# client certificate. It must run privileged: gVisor needs namespaces and
# ptrace or seccomp traps that an unprivileged container cannot set up.

# docker:27.5.1-dind, the multi-arch index, pinned by digest.
ARG DIND_IMAGE=docker:27.5.1-dind@sha256:aa3df78ecf320f5fafdce71c659f1629e96e9de0968305fe1de670e0ca9176ce
FROM ${DIND_IMAGE}

# gVisor release 20261005.0, checked against these SHA-512 sums (taken from
# https://storage.googleapis.com/gvisor/releases/release/20261005.0/<arch>/gvisor.tar.bz2.sha512
# on 2026-10-20). Bump the release and both sums together.
ARG GVISOR_RELEASE=20261005.0
ARG GVISOR_SHA512_AMD64=79869ae9a589355a46d46fdac068d9a954e741741ffe77c64e38c56ea955498c1cb04e9bce44e1cce5155333e6b76bee72347c6661bb7c78fd2fe3aad198c8fe
ARG GVISOR_SHA512_ARM64=1bc58be6f881264ced1df8ff61db0205531abedcace005585ac5637c5c987831062b365f99f270ef0939849f130344b84e0f1a663294406f798b8f7c8cd326ac
# runsc looks for its sidecars (gvisor_sentry, ...) in gvisor-bin/ beside it.
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH:-amd64}" in \
      amd64) arch=x86_64; sum="$GVISOR_SHA512_AMD64" ;; \
      arm64) arch=aarch64; sum="$GVISOR_SHA512_ARM64" ;; \
      *) echo "no gVisor build for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    cd /tmp; \
    wget -q -O gvisor.tar.bz2 "https://storage.googleapis.com/gvisor/releases/release/${GVISOR_RELEASE}/${arch}/gvisor.tar.bz2"; \
    echo "$sum  gvisor.tar.bz2" | sha512sum -c -; \
    tar -xjf gvisor.tar.bz2 -C /usr/local/bin runsc containerd-shim-runsc-v1 gvisor-bin; \
    chown -R root:root /usr/local/bin/runsc /usr/local/bin/containerd-shim-runsc-v1 /usr/local/bin/gvisor-bin; \
    rm -f gvisor.tar.bz2; \
    runsc --version

# runsc is the default and the only runtime the checker accepts (bin/start.sh in
# the checker refuses a daemon whose default runtime is anything else).
COPY daemon.json /etc/docker/daemon.json

# TLS on, always: the entrypoint generates the certificates into these volumes.
ENV DOCKER_TLS_CERTDIR=/certs
VOLUME ["/certs/ca", "/certs/server", "/certs/client", "/var/lib/docker"]
EXPOSE 2376
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD ["docker", "--host", "tcp://127.0.0.1:2376", "--tlsverify", "--tlscacert", "/certs/client/ca.pem", "--tlscert", "/certs/client/cert.pem", "--tlskey", "/certs/client/key.pem", "info", "--format", "{{.DefaultRuntime}}"]

ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=""
LABEL org.opencontainers.image.title="Omega Hub runner daemon" \
      org.opencontainers.image.description="Docker in Docker with gVisor (runsc) as its only runtime, for the Omega Hub checker's sandbox runs." \
      org.opencontainers.image.vendor="Future AGI" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"
