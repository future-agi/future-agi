package sources

import (
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// bundledSnapshots holds snapshots/<upstream_slug>.json for openai_compatible
// sources (snapshots/README.md), written by
// scripts/update-provider-catalogs.sh from the upstream's public model list.
//
//go:embed snapshots
var bundledSnapshots embed.FS

// bundledList is a bundled snapshot: the model list's entries and when and
// where they were fetched.
type bundledList struct {
	FetchedAt time.Time       `json:"fetched_at"`
	SourceURL string          `json:"source_url"`
	Data      json.RawMessage `json:"data"`
}

// bundledLoader returns the reader of the snapshot bundled in fsys for slug,
// for providerID's boot catalog, or nil when there is none.
func bundledLoader(fsys fs.FS, providerID, slug string) func() (*catalog.Snapshot, error) {
	name := "snapshots/" + slug + ".json"
	if _, err := fs.Stat(fsys, name); err != nil {
		return nil
	}
	return func() (*catalog.Snapshot, error) {
		body, err := fs.ReadFile(fsys, name)
		if err != nil {
			return nil, err
		}
		var b bundledList
		if err := json.Unmarshal(body, &b); err != nil {
			return nil, fmt.Errorf("bundled %s: %w", name, err)
		}
		if b.FetchedAt.IsZero() {
			return nil, errors.New("bundled " + name + ": no fetched_at")
		}
		models, err := ParseOpenAICompatible(b.Data, slug)
		if err != nil {
			return nil, fmt.Errorf("bundled %s: %w", name, err)
		}
		return &catalog.Snapshot{ProviderID: providerID, Source: catalog.OriginBundled,
			CatalogSource: catalog.SourceOpenAICompatible, FetchedAt: b.FetchedAt, Models: models}, nil
	}
}
