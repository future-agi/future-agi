// Package connectors is the connector catalog the gateway reads:
// the bundle agentcc-catalog build writes,
// dist/catalog.json (connector-catalog-bundle/v1), embedded at build time, or
// an operator's catalog directory (connectors.catalog_dir) in its place.
// Either is checked as the catalog's own records are: connector-manifest/v1,
// L0 and L1 (the catalog loader). A server added from the catalog (its catalog_ref)
// takes its declared credential parts from it.
//
// dist/** is generated and integrator-owned: the
// integrator regenerates it at every wave merge, and agentcc-catalog check
// fails when it is stale.
package connectors

import (
	_ "embed"
	"errors"
	"fmt"
	"os"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// BundleJSON is dist/catalog.json as built.
//
//go:embed dist/catalog.json
var BundleJSON []byte

// Capture the build-embedded bytes before any external caller can mutate the
// legacy exported slice. Admission and revision evidence use this same copy.
var embeddedCatalogJSON = string(BundleJSON)

// Catalog is a loaded connector catalog and where it came from.
type Catalog struct {
	// Records are the catalog's apps, providers and platforms.
	Records *manifest.Catalog
	// Source names it in logs: the embedded bundle's version and sha256, or
	// the operator's directory.
	Source string
	// Set only by Embedded after validation of the exact embedded bytes.
	runtimeSnapshot *runtimeSnapshot
}

// Embedded decodes the embedded bundle and checks its records, once.
var Embedded = sync.OnceValues(func() (*Catalog, error) {
	data := []byte(embeddedCatalogJSON)
	b, err := bundle.Decode(data)
	if err != nil {
		return nil, fmt.Errorf("the embedded connector catalog (dist/catalog.json): %w", err)
	}
	cat, err := b.Catalog()
	if err != nil {
		return nil, fmt.Errorf("the embedded connector catalog (dist/catalog.json): %w", err)
	}
	snapshot, err := captureRuntimeSnapshot(cat, data)
	if err != nil {
		return nil, err
	}
	return &Catalog{Records: cat, Source: "embedded bundle " + b.Version + " (sha256 " + b.SHA256 + ")", runtimeSnapshot: snapshot}, nil
})

// LoadDir reads an operator's catalog directory (the catalog/connectors
// layout: apps/<app-id>/app.yaml, apps/<app-id>/providers/<provider-id>.yaml,
// platforms/<platform-id>.yaml) and checks its records as agentcc-catalog
// does (the schema and L0, L1); a directory that is missing, not one, or
// holds no app is refused, never read as an empty catalog.
func LoadDir(dir string) (*Catalog, error) {
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		return nil, fmt.Errorf("connectors.catalog_dir %s is not a directory", dir)
	}
	cat, err := manifest.LoadDir(os.DirFS(dir))
	if err != nil {
		return nil, fmt.Errorf("connectors.catalog_dir %s: the records are not valid:\n%w", dir, err)
	}
	if err := manifest.CheckL1(cat); err != nil {
		return nil, fmt.Errorf("connectors.catalog_dir %s: the records fail L1:\n%w", dir, err)
	}
	if len(cat.Apps) == 0 {
		return nil, errors.New("connectors.catalog_dir " + dir + " holds no app (apps/<app-id>/app.yaml)")
	}
	return &Catalog{Records: cat, Source: "connectors.catalog_dir " + dir}, nil
}

// Load is the catalog the gateway uses: the operator's directory when dir is
// set, else the embedded bundle.
func Load(dir string) (*Catalog, error) {
	if dir != "" {
		return LoadDir(dir)
	}
	return Embedded()
}
