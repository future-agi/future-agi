FROM node:20-slim
WORKDIR /srv
COPY packages/ ./packages/
RUN cd packages && sha256sum -c SHA256SUMS && cd .. && \
    npm install --ignore-scripts --no-audit --no-fund ./packages/*.tgz && rm -rf packages
COPY emit.mjs .
USER node
ENTRYPOINT ["node", "/srv/emit.mjs"]
