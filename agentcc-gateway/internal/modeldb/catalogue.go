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
	Entries   int       `json:"entries"`
	LoadedAt  time.Time `json:"loaded_at"`
	LastError string    `json:"last_error,omitempty"` // newest failed fetch; cleared by the next good one
}

// Catalogue owns the layers under the live ModelDB: bundled, then fetched,
// then config overrides. It is the only writer of the shared ModelDB pointer,
// so a fetch and a config reload each rebuild from the other's latest input.
type Catalogue struct {
	mu        sync.Mutex
	models    map[string]*ModelInfo
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

// Replace makes fetched the catalogue under the overrides.
func (c *Catalogue) Replace(fetched map[string]*ModelInfo, url string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.models = fetched
	c.info = CatalogueInfo{Source: SourceFetched, URL: url, Entries: len(fetched), LoadedAt: time.Now()}
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
