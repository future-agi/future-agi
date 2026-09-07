# Local smoke only. The real current collector is cross-compiled by run.py.
# Isolate the two-file context so no manifests, credentials or logs are sent.
FROM gcr.io/distroless/static-debian12:nonroot
COPY fi-collector /usr/local/bin/fi-collector
COPY collector.yaml /etc/fi-collector/config.yaml
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/fi-collector"]
