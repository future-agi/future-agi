FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS install
WORKDIR /app
COPY package.json package-lock.json ./
COPY packages/ ./packages/
RUN --network=none npm ci --offline --ignore-scripts --omit=dev --no-audit --no-fund

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS runtime
ENV NODE_ENV=production OMEGA_EVIDENCE_DIR=/evidence \
    OMEGA_SCRATCH_DIR=/var/lib/omega/scratch OMEGA_REPORT_SPOOL=/var/lib/omega/reports
WORKDIR /app
COPY --from=install /app/node_modules ./node_modules
COPY --from=install /app/package.json ./package.json
COPY worker/ ./worker/
RUN mkdir -p /var/lib/omega/scratch /var/lib/omega/reports && \
    chown node:node /var/lib/omega/scratch /var/lib/omega/reports
USER node
ENTRYPOINT ["node"]
CMD ["worker/daemon.mjs"]
