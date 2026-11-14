# Test-only asset fixture; the product bundle is copied without modification.
ARG BASE_TAG=e2e-local
FROM futureagi/frontend:${BASE_TAG} AS product
FROM node:22-alpine AS assets
WORKDIR /fixture
COPY e2e/stack/frontend/rewrite-index.mjs ./rewrite-index.mjs
COPY --from=product /usr/share/nginx/html/index.html ./index.html
# Match the CDN version pinned by the shipped @monaco-editor/loader. Verify the
# npm archive before extraction; builds may fetch packages, browsers may not.
RUN npm pack monaco-editor@0.43.0 --ignore-scripts \
    && node -e "const fs=require('fs'),c=require('crypto'); const actual=c.createHash('sha512').update(fs.readFileSync('monaco-editor-0.43.0.tgz')).digest('base64'); if(actual!=='cnoqwQi/9fml2Szamv1XbSJieGJ1Dc8tENVMD26Kcfl7xGQWp7OBKMjlwKVGYFJ3/AXJjSOGvcqK7Ry/j9BM1Q==')throw Error('Monaco integrity mismatch')" \
    && tar -xzf monaco-editor-0.43.0.tgz \
    && node rewrite-index.mjs index.html bootstrap.js
FROM product
COPY --from=assets /fixture/package/min/vs /usr/share/nginx/html/e2e-monaco/vs
COPY --from=assets /fixture/index.html /usr/share/nginx/html/index.html
COPY --from=assets /fixture/bootstrap.js /usr/share/nginx/html/e2e-monaco/bootstrap.js
