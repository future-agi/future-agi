FROM node:22.18-alpine AS dependencies

WORKDIR /app/services/mcp
COPY services/mcp/package.json services/mcp/yarn.lock ./
RUN yarn install --frozen-lockfile

FROM dependencies AS build

WORKDIR /app
COPY api_contracts/openapi/swagger.json api_contracts/openapi/swagger.json
COPY api_contracts/mcp api_contracts/mcp
COPY services/mcp services/mcp
WORKDIR /app/services/mcp
RUN yarn check:generated && yarn build

FROM node:22.18-alpine AS production-dependencies

ENV NODE_ENV=production
WORKDIR /app/services/mcp
COPY services/mcp/package.json services/mcp/yarn.lock ./
RUN yarn install --frozen-lockfile --production --ignore-scripts

FROM node:22.18-alpine AS runtime

ENV NODE_ENV=production \
    MCP_PORT=3001
WORKDIR /app/services/mcp
COPY --chown=node:node services/mcp/package.json ./
COPY --chown=node:node --from=production-dependencies /app/services/mcp/node_modules ./node_modules
COPY --chown=node:node --from=build /app/services/mcp/dist ./dist

USER node
EXPOSE 3001
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O - http://127.0.0.1:3001/health >/dev/null || exit 1
CMD ["node", "dist/src/server.js"]
