package modeldb

import (
	"sync"
	"sync/atomic"
	"time"
)

// Catalogue sources, as reported by CatalogueInfo.Source.
const (
	SourceBundled = "bundled"
	SourceFetched = "fetched"
)

// CatalogueInfo says which catalogue the gateway prices from.
type CatalogueInfo struct {
	Source    string    `json:"source"`
	URL       string    `json:"url,omitempty"`
	Entries   int       `json:"entries"`                   // models priced: bundled plus fetched
	Fetched   int       `json:"fetched_entries,omitempty"` // models the last good fetch held
	LoadedAt  time.Time `json:"loaded_at"`
	LastError string    `json:"last_error,omitempty"` // newest failed fetch; cleared by the next good one
}

// Catalogue owns the layers under the live ModelDB: bundled, overlaid by
// fetched (fetched wins on matching keys), then config overrides. A bundled
// model the fetch lacks stays, so a fetch cannot remove a bundled model. Each
// accepted fetch replaces the previous one, so a model only an earlier fetch
// held goes when the next accepted fetch lacks it. It is the only writer of
// the shared ModelDB pointer, so a fetch and a config reload each rebuild from
// the other's latest input.
type Catalogue struct {
	mu        sync.Mutex
	models    map[string]*ModelInfo // bundled overlaid by the last good fetch
	overrides map[string]ModelOverride
	info      CatalogueInfo
	target    *atomic.Pointer[ModelDB]
}

// NewCatalogue starts from the bundled models and stores the first ModelDB in target.
func NewCatalogue(target *atomic.Pointer[ModelDB], overrides map[string]ModelOverride) *Catalogue {
	c := &Catalogue{
		models:    BundledModels,
		overrides: overrides,
		info:      CatalogueInfo{Source: SourceBundled, Entries: len(BundledModels), LoadedAt: time.Now()},
		target:    target,
	}
	c.rebuild()
	return c
}

// SetOverrides swaps the config overrides and keeps the last good catalogue.
func (c *Catalogue) SetOverrides(overrides map[string]ModelOverride) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.overrides = overrides
	c.rebuild()
}

// Overlay lays fetched over the bundled models, under the overrides.
func (c *Catalogue) Overlay(fetched map[string]*ModelInfo, url string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	merged := make(map[string]*ModelInfo, len(BundledModels)+len(fetched))
	for id, m := range BundledModels {
		merged[id] = m
	}
	for id, m := range fetched {
		merged[id] = m
	}
	c.models = merged
	c.info = CatalogueInfo{Source: SourceFetched, URL: url, Entries: len(merged), Fetched: len(fetched), LoadedAt: time.Now()}
	c.rebuild()
}

// Info returns the loaded catalogue's source, size and load time.
func (c *Catalogue) Info() CatalogueInfo {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.info
}

func (c *Catalogue) recordFailure(err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.info.LastError = err.Error()
}

// rebuild must run with c.mu held.
func (c *Catalogue) rebuild() {
	c.target.Store(New(c.models, c.overrides))
}
