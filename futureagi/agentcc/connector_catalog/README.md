# Connector catalog bundle (Django copy)

`catalog.json` is a byte-for-byte copy of the connector catalog bundle, `agentcc-gateway/catalog/connectors/dist/catalog.json` (schema `connector-catalog-bundle/v1`), for the Django app to read.

It is generated. Do not edit it. To regenerate it with the gateway's copy, run this from `agentcc-gateway/`:

```
go run ./cmd/agentcc-catalog build
```

`go run ./cmd/agentcc-catalog check` fails when this copy differs from the bundle, or when any file other than this README and `catalog.json` is in this directory.

The bundle's format, and how to recompute its `sha256` from the file alone, are in `agentcc-gateway/catalog/connectors/dist/README.md`.
