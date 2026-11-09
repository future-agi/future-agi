# Pinned by the multi-arch index digest (READINESS L09): node 22.23.3 on Alpine, built 2026-09-23,
# read from registry-1.docker.io on 2026-10-19. Moving the tag never changes this image; bump the
# digest deliberately (.omega/program/PLATFORM-HELM/gateway-base-image.md has the reads) and re-run its tests.
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402

WORKDIR /app
COPY package.json ./
COPY src ./src
# The route manifest is the gateway's only route list (wall W2); the image refuses to start without it.
# It also holds the route -> product table the product wall reads (src/product-wall.mjs).
COPY manifest ./manifest

ENV NODE_ENV=production
USER node
EXPOSE 8080 8081
# SIGTERM drains (open streams end cleanly, in-flight work gets OMEGA_GATEWAY_DRAIN_MS); set the
# pod's terminationGracePeriodSeconds above that. A private CA for the HTTPS hops to EE and the
# session exchange is trusted through NODE_EXTRA_CA_CERTS (the Helm chart sets it); the image holds none.
STOPSIGNAL SIGTERM
CMD ["node", "src/server.mjs"]
