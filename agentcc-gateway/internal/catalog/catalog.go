// Package catalog holds the model catalogs of the managed providers: every
// model a provider FutureAGI holds the credentials for will serve, with its
// endpoints, full price card, limits, availability state and what it
// supports. Routing reads it to find the provider for a model, the cost
// plugin to price it, auto routing to choose one, and the console to list the
// marketplace. Every change is a new catalog version with its own snapshot
// id, kept so a decision made on it can be replayed.
//
// A provider's catalog comes from one source (Source): OpenRouter's rich
// list, a generic OpenAI-compatible /models list, or a static price card.
// The readers of the last two live in the sources subpackage, which the
// gateway's wiring attaches to each Source. Every model also carries its
// canonical id (canonical.go), under which the listings of one model by
// several sources merge.
package catalog

import (
	"encoding/json"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Model is one model in a provider's catalog.
type Model struct {
	ID          string `json:"id"`
	Name        string `json:"name,omitempty"`
	Description string `json:"description,omitempty"`
	Created     int64  `json:"created,omitempty"`
	// CanonicalSlug is the upstream's own slug for the model (OpenRouter's
	// dated canonical_slug), kept as read.
	CanonicalSlug string `json:"canonical_slug,omitempty"`
	// Canonical is the model's canonical id, a lowercased "vendor/model"
	// (Canonical, canonical.go): one id across every source that lists it.
	// The store derives it; a price card may name it.
	Canonical           string   `json:"canonical,omitempty"`
	ContextLength       int      `json:"context_length,omitempty"`
	MaxOutputTokens     int      `json:"max_output_tokens,omitempty"`
	Modality            string   `json:"modality,omitempty"`
	InputModalities     []string `json:"input_modalities,omitempty"`
	OutputModalities    []string `json:"output_modalities,omitempty"`
	Tokenizer           string   `json:"tokenizer,omitempty"`
	InstructType        string   `json:"instruct_type,omitempty"`
	SupportedParameters []string `json:"supported_parameters,omitempty"`

	// Prices is the model's price card; PriceTiers replace it for long
	// prompts. The store mirrors the prompt, completion and cache-read
	// prices into the per-token fields below, which some callers read.
	Prices            Prices      `json:"prices"`
	PriceTiers        []PriceTier `json:"price_tiers,omitempty"`
	InputPerToken     float64     `json:"input_per_token"`
	OutputPerToken    float64     `json:"output_per_token"`
	CacheReadPerToken float64     `json:"cache_read_per_token,omitempty"`

	// DynamicPrice marks a model whose price depends on what it routes to
	// (OpenRouter's own routers list a negative price). Only the upstream's
	// reported cost can price it.
	DynamicPrice bool `json:"dynamic_price,omitempty"`
	// Unpriced marks a model whose source lists no price at all (a plain
	// /v1/models list); it has no price card and is never offered as managed.
	Unpriced bool `json:"unpriced,omitempty"`
	// StalePrice marks a price-card model whose card is too old to bill by
	// (sources.StaleAfter): its prices are kept for display, but it is not
	// priced and not offered as managed until the card is refreshed.
	StalePrice bool `json:"stale_price,omitempty"`

	// ExpirationDate is the day ("2026-10-31") the upstream retires the model.
	ExpirationDate string `json:"expiration_date,omitempty"`
	// Preview marks a preview, experimental or beta model.
	Preview bool `json:"preview,omitempty"`
	// State is active, degraded, deprecated or blocked (states.go), and
	// StateReason why it is not active. The store derives both.
	State       string `json:"state,omitempty"`
	StateReason string `json:"state_reason,omitempty"`

	// Endpoints are the ways to reach the model; EndpointsFetchedAt is when
	// they were read, and EndpointsOrigin whether from the bundled snapshot
	// or the live upstream.
	Endpoints          []Endpoint `json:"endpoints,omitempty"`
	EndpointsFetchedAt time.Time  `json:"endpoints_fetched_at,omitzero"`
	EndpointsOrigin    string     `json:"endpoints_origin,omitempty"`

	// Benchmarks is the upstream's benchmark object, kept verbatim; nothing
	// scores on it.
	Benchmarks json.RawMessage `json:"benchmarks,omitempty"`
}

// Vendor is the part of the ID before the first "/", or "" when there is none.
func (m *Model) Vendor() string {
	if i := strings.IndexByte(m.ID, '/'); i > 0 {
		return m.ID[:i]
	}
	return ""
}

// Supports reports whether the model lists param among its supported request
// parameters.
func (m *Model) Supports(param string) bool {
	for _, p := range m.SupportedParameters {
		if p == param {
			return true
		}
	}
	return false
}

// Accepts reports whether the model takes the modality as input.
func (m *Model) Accepts(modality string) bool {
	for _, x := range m.InputModalities {
		if x == modality {
			return true
		}
	}
	return false
}

// Produces reports whether the model can output the modality.
func (m *Model) Produces(modality string) bool {
	for _, x := range m.OutputModalities {
		if x == modality {
			return true
		}
	}
	return len(m.OutputModalities) == 0 && modality == "text"
}

// Priced reports whether the model has a fixed, current price card (free
// counts).
func (m *Model) Priced() bool { return !m.DynamicPrice && !m.Unpriced && !m.StalePrice }

// Sellable reports whether managed traffic may be sold the model: it has a
// price the gateway can bill, fixed or reported by the upstream after the
// call (DynamicPrice), and that price is not stale. An unpriced model is
// listed but never offered as managed.
func (m *Model) Sellable() bool { return !m.Unpriced && !m.StalePrice }

// BillsCall reports whether p alone can price a call: it has an input, output
// or per-request price above zero, the test the cost plugin's tokenCost
// applies before pricing from a card. A source whose upstream reports no cost
// (an OpenAI-compatible list, a price card) lists a model whose prices fail it
// as unpriced, never offered as managed.
func (p Prices) BillsCall() bool { return p.Prompt > 0 || p.Completion > 0 || p.Request > 0 }

// Servable reports whether the gateway serves the model at now: it is not
// blocked and its expiration date has not come. A deprecated model serves
// until that day, and a degraded one serves.
func (m *Model) Servable(now time.Time) bool {
	if m.State == StateBlocked {
		return false
	}
	if m.ExpirationDate == "" {
		return true
	}
	exp, err := time.Parse(time.DateOnly, m.ExpirationDate)
	return err != nil || now.Before(exp)
}

// Endpoint returns the model's endpoint with id.
func (m *Model) Endpoint(id string) (*Endpoint, bool) {
	for i := range m.Endpoints {
		if m.Endpoints[i].ID == id {
			return &m.Endpoints[i], true
		}
	}
	return nil, false
}

// syncPrices makes Prices and the per-token fields agree: a model built with
// only the per-token fields gets its Prices from them, and the per-token
// fields always mirror Prices (zero for a dynamic price).
func (m *Model) syncPrices() {
	if m.Prices == (Prices{}) {
		m.Prices = Prices{Prompt: m.InputPerToken, Completion: m.OutputPerToken, CacheRead: m.CacheReadPerToken}
	}
	if m.DynamicPrice {
		m.Prices, m.PriceTiers = Prices{}, nil
	}
	m.InputPerToken, m.OutputPerToken, m.CacheReadPerToken = m.Prices.Prompt, m.Prices.Completion, m.Prices.CacheRead
}

// Snapshot origins: where a provider's model list came from.
const (
	OriginBundled  = "bundled"  // the snapshot built into the gateway
	OriginLive     = "live"     // fetched from the upstream
	OriginRestored = "restored" // read back from catalog.snapshot_dir at boot
	// OriginPriceCard is a static price card, bundled or on disk, read at
	// boot and re-read on every refresh.
	OriginPriceCard = "price_card"
	// originEndpoints marks a change made by an endpoint refresh; the model
	// list keeps its origin.
	originEndpoints = "endpoints"
)

// Snapshot is one provider's catalog as fetched at one moment. Immutable.
type Snapshot struct {
	ProviderID string `json:"provider"`
	// Source is the origin of the model list: bundled, live, restored or
	// price_card.
	Source string `json:"source"`
	// CatalogSource is the kind of source: openrouter, openai_compatible or
	// price_card.
	CatalogSource string            `json:"catalog_source,omitempty"`
	FetchedAt     time.Time         `json:"fetched_at"`
	Models        map[string]*Model `json:"-"`
	// ZDR holds the source's zero-data-retention endpoints, keyed
	// zdrKey(model, tag); nil until a list has been read.
	ZDR map[string]bool `json:"-"`
	// UpstreamFeeBps is the provider's effective upstream fee, the one its
	// endpoints carry; the store sets it.
	UpstreamFeeBps int `json:"-"`
}

// Sorted returns the snapshot's models ordered by ID.
func (s *Snapshot) Sorted() []*Model {
	out := make([]*Model, 0, len(s.Models))
	for _, m := range s.Models {
		out = append(out, m)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Catalog is one version of every managed provider's snapshot. Immutable;
// the Store publishes a new one on every change.
type Catalog struct {
	id         string
	byProvider map[string]*Snapshot

	// merged is the canonical view of this version, built on first use.
	mergeOnce sync.Once
	merged    *mergedView
}

// ID is the catalog version's snapshot id (snapshots.go).
func (c *Catalog) ID() string {
	if c == nil {
		return ""
	}
	return c.id
}

// Get returns the provider's entry for model.
func (c *Catalog) Get(providerID, model string) (*Model, bool) {
	if c == nil {
		return nil, false
	}
	snap := c.byProvider[providerID]
	if snap == nil {
		return nil, false
	}
	m, ok := snap.Models[model]
	return m, ok
}

// Has reports whether the provider's catalog offers model as managed: it
// lists the model, with a price the gateway can bill (Sellable). The managed
// resolver asks it; Get reads every listed model.
func (c *Catalog) Has(providerID, model string) bool {
	m, ok := c.Get(providerID, model)
	return ok && m.Sellable()
}

// ServesManaged reports whether a managed call of model (the id sent
// upstream) on providerID can be billed. OpenRouter reports every call's
// cost, so a provider whose catalog source is openrouter serves any model it
// routes, listed or not (the managed resolver's "<managed id>/<model>" path).
// Any other source's upstream reports none, so it serves only what its
// catalog sells (Has); a provider with no catalog serves nothing. Every place
// that bills a request as managed must ask it.
func (c *Catalog) ServesManaged(providerID, model string) bool {
	if c == nil {
		return false
	}
	if snap := c.byProvider[providerID]; snap != nil && snap.CatalogSource == SourceOpenRouter {
		return true
	}
	return c.Has(providerID, model)
}

// Find returns the first provider (by ID) whose catalog lists model.
func (c *Catalog) Find(model string) (providerID string, m *Model, ok bool) {
	for _, s := range c.Snapshots() {
		if m, ok := s.Models[model]; ok {
			return s.ProviderID, m, true
		}
	}
	return "", nil, false
}

// Snapshots returns every provider's snapshot, ordered by provider ID.
func (c *Catalog) Snapshots() []*Snapshot {
	if c == nil {
		return nil
	}
	out := make([]*Snapshot, 0, len(c.byProvider))
	for _, s := range c.byProvider {
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ProviderID < out[j].ProviderID })
	return out
}

// Len is the number of models across every provider.
func (c *Catalog) Len() int {
	if c == nil {
		return 0
	}
	n := 0
	for _, s := range c.byProvider {
		n += len(s.Models)
	}
	return n
}

// Options configure a Store. Zero sizes, intervals and rates take the
// defaults, except EndpointsRefresh, where zero turns the crawler off.
type Options struct {
	// HistorySize is how many catalog versions the history keeps.
	HistorySize int
	// KeepSnapshots is how many versions' full content is kept.
	KeepSnapshots int
	// SnapshotDir keeps snapshots, history and runtime blocks across
	// restarts; empty keeps them in memory only.
	SnapshotDir string
	// BlockedModels are model ids (or globs, "*" matching anything) blocked
	// by config (catalog.blocked_models).
	BlockedModels []string
	// RefreshInterval is how often the model lists refresh
	// (managed.catalog_refresh): the catalog is stale after three.
	RefreshInterval time.Duration
	// EndpointsRefresh is how often the endpoint crawler runs; zero or less
	// turns it off. EndpointsRPS caps its request rate.
	EndpointsRefresh time.Duration
	EndpointsRPS     float64
	// Providers are the catalog providers config names; a persisted
	// catalog is restored only for them.
	Providers []string
	// UpstreamFeeBps is each provider's effective upstream fee
	// (providers.<id>.upstream_fee_bps); a provider it does not name gets its
	// source's default.
	UpstreamFeeBps map[string]int
	// Now is the clock; nil is time.Now.
	Now func() time.Time
}

// Defaults of a Store that was never configured, matching config.yaml's.
const (
	defaultHistorySize     = 50
	defaultKeepSnapshots   = 10
	defaultRefreshInterval = 6 * time.Hour
	defaultEndpointsRPS    = 5
)

// Store holds the current Catalog. Reads are lock-free; writes are
// serialised, and each one publishes a new version.
type Store struct {
	mu  sync.Mutex
	cur atomic.Pointer[Catalog]

	opts Options
	// base is each provider's normalized snapshot, before states, and
	// baseID its snapshot id.
	base   map[string]*Snapshot
	baseID string

	history []HistoryEntry // oldest first
	keptIDs []string       // versions whose content is kept, oldest first
	keptMem map[string]*Catalog

	runtimeBlocks map[string]string // model id or glob -> reason
	degraded      map[string]string // model id -> reason (SetModelState)

	lastAdminRefresh map[string]time.Time
	refresher        *Refresher
	persist          *persister
}

// NewStore returns an empty store with the default options.
func NewStore() *Store {
	s := &Store{
		opts: Options{
			HistorySize:     defaultHistorySize,
			KeepSnapshots:   defaultKeepSnapshots,
			RefreshInterval: defaultRefreshInterval,
			EndpointsRPS:    defaultEndpointsRPS,
		},
		base:             map[string]*Snapshot{},
		keptMem:          map[string]*Catalog{},
		runtimeBlocks:    map[string]string{},
		degraded:         map[string]string{},
		lastAdminRefresh: map[string]time.Time{},
	}
	s.baseID = snapshotID(nil)
	s.cur.Store(&Catalog{id: s.baseID, byProvider: map[string]*Snapshot{}})
	return s
}

// Configure applies opts: the persisted history, snapshots and runtime
// blocks are read back from opts.SnapshotDir, and the current catalog is
// re-derived (fees, states), and replaced by the newest persisted version
// for any provider it is fresher for.
func (s *Store) Configure(opts Options) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if opts.HistorySize <= 0 {
		opts.HistorySize = defaultHistorySize
	}
	if opts.KeepSnapshots <= 0 {
		opts.KeepSnapshots = defaultKeepSnapshots
	}
	if opts.RefreshInterval <= 0 {
		opts.RefreshInterval = defaultRefreshInterval
	}
	if opts.EndpointsRPS <= 0 {
		opts.EndpointsRPS = defaultEndpointsRPS
	}
	s.opts = opts
	if opts.SnapshotDir != "" {
		p, err := newPersister(opts.SnapshotDir)
		if err != nil {
			return err
		}
		s.persist = p
		s.loadPersistedLocked()
	}
	for id, snap := range s.base {
		s.base[id] = s.normalizeLocked(id, snap)
	}
	s.baseID = snapshotID(s.base)
	s.publishLocked("", OriginRestored, nil)
	if cur := s.cur.Load(); !contains(s.keptIDs, cur.id) {
		s.keepLocked(cur)
	}
	return nil
}

// now is the store's clock.
func (s *Store) now() time.Time {
	if s.opts.Now != nil {
		return s.opts.Now()
	}
	return time.Now()
}

// Load returns the current catalog. Safe on a nil store.
func (s *Store) Load() *Catalog {
	if s == nil {
		return nil
	}
	return s.cur.Load()
}

// Put replaces one provider's snapshot. A change to the catalog's content
// is a new version, recorded in the history unless the snapshot is the
// bundled or restored one.
func (s *Store) Put(snap *Snapshot) {
	s.update(snap.ProviderID, snap.Source, func(*Snapshot) *Snapshot { return snap })
}

// update replaces providerID's snapshot with what fn makes of the current
// one (nil when there is none) and publishes the result. It reports whether
// the catalog version changed.
func (s *Store) update(providerID, origin string, fn func(prev *Snapshot) *Snapshot) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	prev := s.base[providerID]
	next := fn(prev)
	if next == nil {
		return false
	}
	s.base[providerID] = s.normalizeLocked(providerID, next)
	s.baseID = snapshotID(s.base)
	return s.publishLocked(providerID, origin, prev)
}

// normalizeLocked copies snap as providerID's, with every derived field
// set: prices in both forms, the preview flag, the canonical id, and each
// endpoint's provider, source, fee and ZDR flag. States are left to
// publishLocked.
func (s *Store) normalizeLocked(providerID string, snap *Snapshot) *Snapshot {
	out := *snap
	out.ProviderID = providerID
	out.UpstreamFeeBps = s.feeLocked(providerID, snap.CatalogSource)
	out.Models = make(map[string]*Model, len(snap.Models))
	for id, m := range snap.Models {
		mc := *m
		mc.ID = id
		mc.syncPrices()
		mc.Preview = isPreview(mc.ID, mc.Name)
		mc.Canonical = canonicalOf(providerID, snap.CatalogSource, &mc)
		mc.State, mc.StateReason = "", ""
		if len(m.Endpoints) > 0 {
			eps := make([]Endpoint, len(m.Endpoints))
			for i, e := range m.Endpoints {
				e.Provider = providerID
				if e.Source == "" {
					e.Source = snap.CatalogSource
				}
				e.UpstreamFeeBps = s.feeLocked(providerID, e.Source)
				applyZDR(&e, mc.ID, snap.ZDR)
				eps[i] = e
			}
			mc.Endpoints = eps
		}
		out.Models[id] = &mc
	}
	return &out
}

// feeLocked is providerID's upstream fee for an endpoint from source.
func (s *Store) feeLocked(providerID, source string) int {
	if fee, ok := s.opts.UpstreamFeeBps[providerID]; ok {
		return fee
	}
	return defaultUpstreamFeeBps(source)
}

// publishLocked derives every model's state, publishes the catalog, and,
// when its snapshot id changed, keeps the version and records providerID's
// change from prev in the history (unless origin is bundled or restored).
func (s *Store) publishLocked(providerID, origin string, prev *Snapshot) bool {
	old := s.cur.Load()
	next := &Catalog{id: s.baseID, byProvider: make(map[string]*Snapshot, len(s.base))}
	now := s.now()
	for id, snap := range s.base {
		next.byProvider[id] = s.withStatesLocked(snap, now)
	}
	s.cur.Store(next)
	if next.id == old.id {
		return false
	}
	s.keepLocked(next)
	if origin != OriginBundled && origin != OriginRestored && providerID != "" {
		s.recordLocked(origin, providerID, prev, next)
	}
	return true
}

// MatchGlob reports whether s matches pattern, where "*" matches any run of
// characters, "/" included: the glob catalog.blocked_models and an org's
// managed allow and deny lists use.
func MatchGlob(pattern, s string) bool { return globMatch(pattern, s) }

// View is p as decimal strings in OpenRouter's names, the pricing object of
// catalog-endpoints-v1.
func (p Prices) View() PricingView { return pricingView(p) }

// logPersistError reports a snapshot_dir write that failed; the catalog in
// memory is unaffected.
func logPersistError(what string, err error) {
	if err != nil {
		slog.Warn("catalog: could not persist "+what, "error", err)
	}
}
