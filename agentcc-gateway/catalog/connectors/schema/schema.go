// Package schema embeds the connector catalog's published JSON Schemas
// (draft 2020-12): connector-manifest/v1 for app, provider and platform
// records, connector-catalog-bundle/v1 for dist/catalog.json, and
// connector-price-overlay/v1 for the gateway-only price overlay.
//
// internal/connectorcatalog/manifest is the strict loader for the manifest;
// its TestSchemaParity keeps the loader's keys, required keys, patterns,
// enums and bounds equal to these documents.
package schema

import "embed"

// File names of the embedded schemas.
const (
	Manifest = "connector-manifest.v1.json"
	Bundle   = "connector-catalog-bundle.v1.json"
	Overlay  = "connector-price-overlay.v1.json"
)

// Files holds the three schema documents, by file name.
//
//go:embed *.json
var Files embed.FS
