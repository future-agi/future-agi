FROM node:20-slim
WORKDIR /srv
COPY packages/ packages.sha256 ./packages/
RUN cd packages && sha256sum -c packages.sha256 && cd .. && \
    npm install --ignore-scripts --no-audit --no-fund ./packages/*.tgz && rm -rf packages
COPY emit.mjs .
USER node
ENTRYPOINT ["node", "/srv/emit.mjs"]
