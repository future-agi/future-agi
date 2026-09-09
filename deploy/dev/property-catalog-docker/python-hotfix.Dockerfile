ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# The archive is built from reviewed workspace paths relative to /app/backend.
# Keeping this as a separate layer preserves the source-bound DEV candidate
# while allowing a narrowly scoped compatibility/performance retry image.
ADD python-runtime-overlay.tar /app/backend/
