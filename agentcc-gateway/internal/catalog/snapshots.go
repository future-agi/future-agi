package catalog

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"sort"
	"strings"
	"time"
)

// maxDiffItems caps each list in a history entry's diff.
const maxDiffItems = 200

// HistoryEntry is one catalog version in the history: what produced it and
// how it differs from the version before.
type HistoryEntry struct {
	ID string `json:"id"`
	// Provider is the provider whose catalog changed.
	Provider string `json:"provider"`
	// Source is what changed it: "live" (a model-list refresh) or
	// "endpoints" (an endpoint refresh).
	Source          string    `json:"source"`
	FetchedAt       time.Time `json:"fetched_at"`
	ModelCount      int       `json:"model_count"`
	EndpointCount   int       `json:"endpoint_count"`
	PricedEndpoints int       `json:"priced_endpoints"`
	Diff            Diff      `json:"diff"`
}

// Diff is how one provider's catalog changed: models added, removed, and
// those whose own or any endpoint's price changed. Each list holds at most
// maxDiffItems ids; the counts are the full totals.
type Diff struct {
	Added             []string `json:"added"`
	Removed           []string `json:"removed"`
	PriceChanged      []string `json:"price_changed"`
	AddedCount        int      `json:"added_count"`
	RemovedCount      int      `json:"removed_count"`
	PriceChangedCount int      `json:"price_changed_count"`
}

// snapshotID is the first 12 hex characters of the sha256 of the catalog's
// normalized content: each provider's models, their endpoints and prices.
// What changes without the catalog changing is left out: when it was
// fetched, live health (status, uptime, latency, throughput), states, fees
// and descriptive text.
func snapshotID(base map[string]*Snapshot) string {
	type endpoint struct {
		ID, Quantization                  string
		Regions, Params                   []string
		Context, MaxCompletion, MaxPrompt int
		Prices                            Prices
		Tiers                             []PriceTier
		Dynamic, ImplicitCaching          bool
		ZDR                               *bool
		DataCollection                    string
	}
	type model struct {
		ID, Canonical, Expiration string
		Context, MaxOutput        int
		In, Out, Params           []string
		Prices                    Prices
		Tiers                     []PriceTier
		Dynamic, Unpriced         bool
		Endpoints                 []endpoint
	}
	type provider struct {
		ID, Kind string
		Models   []model
	}
	ids := make([]string, 0, len(base))
	for id := range base {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	doc := make([]provider, 0, len(ids))
	for _, id := range ids {
		snap := base[id]
		p := provider{ID: id, Kind: snap.CatalogSource}
		for _, m := range snap.Sorted() {
			nm := model{ID: m.ID, Canonical: m.CanonicalSlug, Expiration: m.ExpirationDate,
				Context: m.ContextLength, MaxOutput: m.MaxOutputTokens,
				In: orNil(m.InputModalities), Out: orNil(m.OutputModalities), Params: orNil(m.SupportedParameters),
				Prices: m.Prices, Tiers: orNil(m.PriceTiers), Dynamic: m.DynamicPrice, Unpriced: m.Unpriced}
			eps := append([]Endpoint(nil), m.Endpoints...)
			sort.Slice(eps, func(i, j int) bool { return eps[i].ID < eps[j].ID })
			for _, e := range eps {
				nm.Endpoints = append(nm.Endpoints, endpoint{ID: e.ID, Quantization: e.Quantization,
					Regions: orNil(e.Regions), Params: orNil(e.SupportedParams), Context: e.ContextLength,
					MaxCompletion: e.MaxCompletion, MaxPrompt: e.MaxPrompt, Prices: e.Prices, Tiers: orNil(e.PriceTiers),
					Dynamic: e.DynamicPrice, ImplicitCaching: e.SupportsImplicitCaching, ZDR: e.ZDR,
					DataCollection: e.DataCollection})
			}
			p.Models = append(p.Models, nm)
		}
		doc = append(doc, p)
	}
	b, _ := json.Marshal(doc) // plain structs: cannot fail
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])[:12]
}

// orNil is s, or nil when s is empty: the persisted JSON drops empty lists,
// so an empty and a missing list must hash alike for a version read back
// from the snapshot directory to keep its id.
func orNil[T any](s []T) []T {
	if len(s) == 0 {
		return nil
	}
	return s
}

// snapshotIDPattern is what a snapshot id looks like; anything else is never
// looked up on disk.
var snapshotIDPattern = regexp.MustCompile(`^[0-9a-f]{12}$`)

// diffSnapshots is how next differs from prev (nil: everything is added).
func diffSnapshots(prev, next *Snapshot) Diff {
	var added, removed, changed []string
	var old map[string]*Model
	if prev != nil {
		old = prev.Models
	}
	for id, m := range next.Models {
		pm, ok := old[id]
		switch {
		case !ok:
			added = append(added, id)
		case pricesDiffer(pm, m):
			changed = append(changed, id)
		}
	}
	for id := range old {
		if _, ok := next.Models[id]; !ok {
			removed = append(removed, id)
		}
	}
	d := Diff{AddedCount: len(added), RemovedCount: len(removed), PriceChangedCount: len(changed)}
	d.Added, d.Removed, d.PriceChanged = capSorted(added), capSorted(removed), capSorted(changed)
	return d
}

// pricesDiffer reports whether a model's price card, or that of an endpoint
// present in both versions, changed.
func pricesDiffer(a, b *Model) bool {
	if a.Prices != b.Prices || a.DynamicPrice != b.DynamicPrice || !tiersEqual(a.PriceTiers, b.PriceTiers) {
		return true
	}
	for i := range b.Endpoints {
		e := &b.Endpoints[i]
		if pe, ok := a.Endpoint(e.ID); ok && (pe.Prices != e.Prices || pe.DynamicPrice != e.DynamicPrice || !tiersEqual(pe.PriceTiers, e.PriceTiers)) {
			return true
		}
	}
	return false
}

func tiersEqual(a, b []PriceTier) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func capSorted(ids []string) []string {
	sort.Strings(ids)
	if len(ids) > maxDiffItems {
		ids = ids[:maxDiffItems]
	}
	if ids == nil {
		ids = []string{}
	}
	return ids
}

// recordLocked appends the history entry for next, trimmed to HistorySize,
// and persists the history.
func (s *Store) recordLocked(origin, providerID string, prev *Snapshot, next *Catalog) {
	stats := next.stats()
	entry := HistoryEntry{
		ID: next.id, Provider: providerID, Source: origin, FetchedAt: s.now(),
		ModelCount: next.Len(), EndpointCount: stats.Endpoints, PricedEndpoints: stats.PricedEndpoints,
		Diff: diffSnapshots(prev, s.base[providerID]),
	}
	if origin == OriginLive {
		entry.FetchedAt = s.base[providerID].FetchedAt
	}
	s.history = append(s.history, entry)
	if n := len(s.history) - s.opts.HistorySize; n > 0 {
		s.history = append([]HistoryEntry(nil), s.history[n:]...)
	}
	if s.persist != nil {
		logPersistError("history", s.persist.saveHistory(s.history))
	}
}

// keepLocked keeps c's content, in memory and in the snapshot directory,
// dropping the oldest beyond KeepSnapshots.
func (s *Store) keepLocked(c *Catalog) {
	s.keptMem[c.id] = c
	s.keptIDs = append(removeString(s.keptIDs, c.id), c.id)
	for len(s.keptIDs) > s.opts.KeepSnapshots {
		delete(s.keptMem, s.keptIDs[0])
		s.keptIDs = s.keptIDs[1:]
	}
	if s.persist != nil {
		logPersistError("snapshot "+c.id, s.persist.saveSnapshot(c))
		logPersistError("snapshot pruning", s.persist.prune(s.keptIDs))
	}
}

func removeString(list []string, v string) []string {
	out := list[:0:0]
	for _, x := range list {
		if x != v {
			out = append(out, x)
		}
	}
	return out
}

// History returns up to limit history entries, newest first (all when limit
// is zero or less).
func (s *Store) History(limit int) []HistoryEntry {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := len(s.history)
	if limit <= 0 || limit > n {
		limit = n
	}
	out := make([]HistoryEntry, 0, limit)
	for i := n - 1; i >= n-limit; i-- {
		out = append(out, s.history[i])
	}
	return out
}

// Snapshot returns the content of catalog version id, if it is one of the
// kept versions.
func (s *Store) Snapshot(id string) (*Catalog, bool) {
	if !snapshotIDPattern.MatchString(id) {
		return nil, false
	}
	s.mu.Lock()
	if c, ok := s.keptMem[id]; ok {
		s.mu.Unlock()
		return c, true
	}
	kept := contains(s.keptIDs, id)
	p := s.persist
	s.mu.Unlock()
	if !kept || p == nil {
		return nil, false
	}
	c, err := p.loadSnapshot(id)
	if err != nil {
		logPersistError("snapshot "+id+" (read)", err)
		return nil, false
	}
	return c, true
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// Age is how old the catalog's oldest provider snapshot is (zero when the
// catalog is empty).
func (s *Store) Age() time.Duration {
	snaps := s.Load().Snapshots()
	if len(snaps) == 0 {
		return 0
	}
	oldest := snaps[0].FetchedAt
	for _, snap := range snaps[1:] {
		if snap.FetchedAt.Before(oldest) {
			oldest = snap.FetchedAt
		}
	}
	s.mu.Lock()
	now := s.now()
	s.mu.Unlock()
	return now.Sub(oldest)
}

// Stale reports whether the catalog is older than three refresh intervals:
// its refreshes have been failing, so prices may be out of date.
func (s *Store) Stale() bool {
	s.mu.Lock()
	interval := s.opts.RefreshInterval
	s.mu.Unlock()
	return len(s.Load().Snapshots()) > 0 && s.Age() > 3*interval
}

// Stats counts a catalog version.
type Stats struct {
	// CanonicalModels counts distinct models: variants (":free") and
	// providers listing the same model count once.
	CanonicalModels int                    `json:"canonical_models"`
	Endpoints       int                    `json:"endpoints"`
	PricedEndpoints int                    `json:"priced_endpoints"`
	FreeModels      int                    `json:"free_models"`
	PerSource       map[string]SourceStats `json:"per_source"`
}

// SourceStats counts one catalog source's part of a catalog.
type SourceStats struct {
	// CanonicalModels counts the source's distinct canonical models.
	CanonicalModels int `json:"canonical_models"`
	Models          int `json:"models"`
	Endpoints       int `json:"endpoints"`
	PricedEndpoints int `json:"priced_endpoints"`
	FreeModels      int `json:"free_models"`
}

// Stats counts the current catalog.
func (s *Store) Stats() Stats { return s.Load().stats() }

func (c *Catalog) stats() Stats {
	st := Stats{PerSource: map[string]SourceStats{}}
	canonical := map[string]bool{}
	perSource := map[string]map[string]bool{}
	for _, snap := range c.Snapshots() {
		kind := snap.CatalogSource
		if kind == "" {
			kind = "unknown"
		}
		ss := st.PerSource[kind]
		if perSource[kind] == nil {
			perSource[kind] = map[string]bool{}
		}
		for _, m := range snap.Models {
			canonical[canonicalKey(m)] = true
			perSource[kind][canonicalKey(m)] = true
			ss.Models++
			if m.Priced() && m.Prices.Prompt == 0 && m.Prices.Completion == 0 {
				ss.FreeModels++
			}
			for i := range m.Endpoints {
				ss.Endpoints++
				if m.Endpoints[i].priced() {
					ss.PricedEndpoints++
				}
			}
		}
		ss.CanonicalModels = len(perSource[kind])
		st.PerSource[kind] = ss
		st.Endpoints += ss.Endpoints
		st.PricedEndpoints += ss.PricedEndpoints
		st.FreeModels += ss.FreeModels
	}
	st.CanonicalModels = len(canonical)
	return st
}

// canonicalKey is the model a catalog entry is a listing of: its canonical
// id (canonical.go), else its canonical slug, else its id without a
// ":variant" suffix.
func canonicalKey(m *Model) string {
	if m.Canonical != "" {
		return m.Canonical
	}
	if m.CanonicalSlug != "" {
		return m.CanonicalSlug
	}
	if i := strings.IndexByte(m.ID, ':'); i > 0 {
		return m.ID[:i]
	}
	return m.ID
}
